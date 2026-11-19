package com.obliphur.oit.core

import android.content.Context
import android.media.midi.MidiDevice
import android.media.midi.MidiDeviceInfo
import android.media.midi.MidiManager
import android.media.midi.MidiOutputPort
import android.media.midi.MidiReceiver
import android.os.Handler
import android.os.Looper
import kotlin.math.max

/** Android MIDI 输入；按首个有效起音计时，保留松键、踏板和其他通道消息。 */
class MusicMidiInput(private val ctx: Context) {
    data class Port(val device: MidiDeviceInfo, val number: Int, val name: String, val openedBluetooth: MidiDevice? = null)
    private val manager = ctx.getSystemService(Context.MIDI_SERVICE) as? MidiManager
    private val bluetooth = ctx.getSystemService(Context.BLUETOOTH_SERVICE) as android.bluetooth.BluetoothManager
    private var device: MidiDevice? = null
    private var port: MidiOutputPort? = null
    private var generation = 0
    private var started = 0L
    private var last = 0L
    private var status = 0
    private var sysex = false
    private val pending = ArrayList<Byte>()
    private val events = ArrayList<Pair<Double, ByteArray>>()
    private val bluetoothPorts = LinkedHashMap<String, MidiDevice>()
    private val openingBluetooth = HashSet<String>()
    @Volatile var recording = false; private set
    @Volatile var error = ""; private set
    @Volatile var noteCount = 0; private set
    fun ports(): List<Port> = manager?.devices.orEmpty().flatMap { info ->
        info.ports.filter { it.type == MidiDeviceInfo.PortInfo.TYPE_OUTPUT }.map { p ->
            Port(info, p.portNumber, (info.properties.getString(MidiDeviceInfo.PROPERTY_NAME) ?: "MIDI") + " · " + p.name.ifEmpty { (p.portNumber + 1).toString() })
        }
    } + synchronized(this) {
        bluetoothPorts.flatMap { (name, opened) -> (0 until opened.info.outputPortCount).map { number ->
            Port(opened.info, number, "$name · ${number + 1}", opened)
        } }
    }
    @Suppress("MissingPermission")
    fun scanBluetooth(): Boolean {
        if (android.os.Build.VERSION.SDK_INT < 23) return false
        if (android.os.Build.VERSION.SDK_INT >= 31 &&
            (androidx.core.content.ContextCompat.checkSelfPermission(ctx, android.Manifest.permission.BLUETOOTH_SCAN) != android.content.pm.PackageManager.PERMISSION_GRANTED ||
             androidx.core.content.ContextCompat.checkSelfPermission(ctx, android.Manifest.permission.BLUETOOTH_CONNECT) != android.content.pm.PackageManager.PERMISSION_GRANTED)) return false
        val adapter = bluetooth.adapter ?: return false
        if (!adapter.isEnabled) return false
        if (adapter.isDiscovering) adapter.cancelDiscovery()
        // BLE MIDI Service Data (03 B8 80), Bluetooth MIDI 1.0; discovery callback publishes MidiDeviceInfo.
        val filter = android.bluetooth.le.ScanFilter.Builder().setServiceData(
            android.os.ParcelUuid.fromString("03b80e5a-ede8-4b33-a751-6ce34ec4c700"), byteArrayOf(), byteArrayOf()).build()
        stopBluetoothScan()
        synchronized(this) {
            bluetoothPorts.values.forEach { runCatching { it.close() } }
            bluetoothPorts.clear()
            openingBluetooth.clear()
        }
        return runCatching {
            adapter.bluetoothLeScanner?.startScan(listOf(filter),
                android.bluetooth.le.ScanSettings.Builder().setScanMode(android.bluetooth.le.ScanSettings.SCAN_MODE_LOW_LATENCY).build(),
                scanCallback)
            adapter.bluetoothLeScanner != null
        }.getOrDefault(false)
    }
    @Suppress("MissingPermission")
    fun stopBluetoothScan() {
        if (android.os.Build.VERSION.SDK_INT >= 31 && androidx.core.content.ContextCompat.checkSelfPermission(ctx, android.Manifest.permission.BLUETOOTH_SCAN) != android.content.pm.PackageManager.PERMISSION_GRANTED) return
        runCatching { bluetooth.adapter?.bluetoothLeScanner?.stopScan(scanCallback) }
    }
    private val scanCallback = object : android.bluetooth.le.ScanCallback() {
        override fun onScanResult(callbackType: Int, result: android.bluetooth.le.ScanResult) {
            val remote = result.device ?: return
            if (android.os.Build.VERSION.SDK_INT >= 31 && androidx.core.content.ContextCompat.checkSelfPermission(
                    ctx, android.Manifest.permission.BLUETOOTH_CONNECT) != android.content.pm.PackageManager.PERMISSION_GRANTED) return
            if (manager == null || android.os.Build.VERSION.SDK_INT < 33) return
            synchronized(this@MusicMidiInput) {
                if (remote.address in bluetoothPorts || !openingBluetooth.add(remote.address)) return
            }
            manager.openBluetoothDevice(remote, { opened ->
                synchronized(this@MusicMidiInput) {
                    openingBluetooth.remove(remote.address)
                    if (opened == null) error = "无法连接蓝牙 MIDI 设备"
                    else if (remote.address in bluetoothPorts) runCatching { opened.close() }
                    else bluetoothPorts[remote.address] = opened
                }
            }, Handler(Looper.getMainLooper()))
        }
        override fun onScanFailed(errorCode: Int) { error = "蓝牙 MIDI 扫描失败 ($errorCode)" }
    }
    @Synchronized fun start(selected: Port) {
        stopBluetoothScan()
        cancel(); error = ""; noteCount = 0; started = 0; last = 0; status = 0; sysex = false; pending.clear(); events.clear()
        recording = true; val ticket = generation
        if (selected.openedBluetooth != null) {
            try {
                device = selected.openedBluetooth
                port = checkNotNull(device!!.openOutputPort(selected.number))
                port!!.connect(receiver(ticket))
            } catch (e: Exception) { error = e.message.orEmpty(); cancel() }
            return
        }
        manager?.openDevice(selected.device, { opened ->
            synchronized(this) {
                if (!recording || ticket != generation) { opened?.close(); return@synchronized }
                try {
                    device = checkNotNull(opened) { "无法打开 MIDI 设备" }
                    port = checkNotNull(opened.openOutputPort(selected.number)) { "无法打开 MIDI 输入端口" }
                    port!!.connect(receiver(ticket))
                } catch (e: Exception) { error = e.message.orEmpty(); cancel() }
            }
        }, Handler(Looper.getMainLooper())) ?: run { recording = false; error = "此设备不支持 MIDI 输入" }
    }
    private fun receiver(ticket: Int) = object : MidiReceiver() {
        override fun onSend(data: ByteArray, offset: Int, count: Int, timestamp: Long) {
            synchronized(this@MusicMidiInput) { if (recording && ticket == generation) feed(data, offset, count, timestamp) }
        }
    }
    private fun feed(data: ByteArray, offset: Int, count: Int, timestamp: Long) {
        for (i in offset until offset + count) {
            val byte = data[i].toInt() and 255
            if (byte >= 248) continue
            if (byte >= 128) {
                pending.clear()
                if (byte == 240) { sysex = true; status = 0; continue }
                if (byte == 247) { sysex = false; status = 0; continue }
                sysex = false
                status = if (byte in 128..239) byte else 0
                continue
            }
            if (sysex || status == 0) continue
            pending.add(byte.toByte())
            val size = if (status and 240 in setOf(192, 208)) 1 else 2
            if (pending.size == size) {
                val event = byteArrayOf(status.toByte()) + pending.toByteArray(); pending.clear()
                val clock = if (timestamp > 0) timestamp else System.nanoTime()
                if (started == 0L && status and 240 == 144 && event[2] != 0.toByte()) started = clock
                if (started == 0L) continue
                last = max(last, clock)
                if (events.size >= Cfg.MUSIC_MIDI_INPUT_MAX_EVENTS) { error = "MIDI 录制超过事件上限"; cancel(); return }
                events.add(max(0.0, (clock - started) / 1e9) to event)
                if (status and 240 == 144 && event[2] != 0.toByte()) noteCount++
            }
        }
    }
    @Synchronized fun stop(): ByteArray {
        val duration = if (started > 0) max(last, System.nanoTime()) - started else 0L
        cancel()
        require(error.isEmpty()) { error }
        require(noteCount > 0) { "尚未收到 MIDI 音符，请重新录制" }
        val end = duration / 1e9
        val active = HashMap<Int, Int>()
        events.forEach { (_, event) ->
            val kind = event[0].toInt() and 240
            if (kind in setOf(128, 144)) {
                val key = (event[0].toInt() and 15) * 128 + event[1].toInt()
                active[key] = max(0, (active[key] ?: 0) + if (kind == 144 && event[2] != 0.toByte()) 1 else -1)
            }
        }
        active.forEach { (key, n) -> repeat(n) { events.add(end to byteArrayOf((128 + key / 128).toByte(), (key % 128).toByte(), 0)) } }
        for (channel in 0..15) events.add(end to byteArrayOf((176 + channel).toByte(), 64, 0))
        return MusicMidi.recording(events.sortedBy { it.first }, end)
    }
    @Synchronized fun cancel() {
        recording = false; generation++
        runCatching { port?.close() }; port = null
        val active = device
        if (active != null && bluetoothPorts.values.none { it === active }) runCatching { active.close() }
        device = null
    }

    @Synchronized fun close() {
        stopBluetoothScan()
        cancel()
        bluetoothPorts.values.forEach { runCatching { it.close() } }
        bluetoothPorts.clear()
        openingBluetooth.clear()
    }
}
