#!/usr/bin/env node
import path from 'path';
import readline from 'readline';
import { initDb } from './lib/db.mjs';
import { tools, handleTool } from './lib/mcp-tools.mjs';

const DB_PATH = process.env.COREAD_DB || path.join(process.cwd(), 'data', 'coread.db');
initDb(DB_PATH);

// MCP stdio transport: one JSON-RPC message per line, no headers.
function send(msg) {
  process.stdout.write(JSON.stringify(msg) + '\n');
}

readline.createInterface({ input: process.stdin }).on('line', line => {
  if (!line.trim()) return;
  let msg;
  try { msg = JSON.parse(line); } catch { send({ jsonrpc: '2.0', id: null, error: { code: -32700, message: 'Parse error' } }); return; }
  handleMessage(msg);
});

function handleMessage(msg) {
  if (msg.method === 'initialize') {
    send({ jsonrpc: '2.0', id: msg.id, result: {
      protocolVersion: '2024-11-05',
      capabilities: { tools: {} },
      serverInfo: { name: 'coread', version: '0.1.0' },
    }});
  } else if (msg.method === 'ping') {
    send({ jsonrpc: '2.0', id: msg.id, result: {} });
  } else if (msg.method === 'tools/list') {
    send({ jsonrpc: '2.0', id: msg.id, result: { tools } });
  } else if (msg.method === 'tools/call') {
    const { name, arguments: args } = msg.params;
    try {
      const result = handleTool(name, args || {});
      send({ jsonrpc: '2.0', id: msg.id, result: {
        content: [{ type: 'text', text: JSON.stringify(result, null, 2) }],
      }});
    } catch (e) {
      send({ jsonrpc: '2.0', id: msg.id, result: {
        content: [{ type: 'text', text: `Error: ${e.message}` }],
        isError: true,
      }});
    }
  } else if (msg.id !== undefined) {
    send({ jsonrpc: '2.0', id: msg.id, error: { code: -32601, message: 'Method not found' } });
  }
}
