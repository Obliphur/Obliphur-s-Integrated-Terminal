package com.obliphur.oit.core

import com.obliphur.oit.ai.Ai
import com.obliphur.oit.ai.Profile
import com.obliphur.oit.ai.ToolProtocol
import org.json.JSONObject
import java.io.File
import java.io.IOException

/** 用独立请求编写草稿。手写简介只在本机合入草稿。 */
object PersonaAuthor {
    const val PROFILE_SUFFIX = "_profile.txt"
    fun isProfile(name: String) = name.endsWith(PROFILE_SUFFIX, ignoreCase = true)

    data class IntimacyLevel(
        val level: Int,
        val title: String,
        val description: String,
        val modelFit: String,
        val promptRule: String,
    )

    const val DEFAULT_INTIMACY_LEVEL = 3

    val CONSENT_MODE_LEVELS = setOf(3, 4)

    const val CONSENT_MODE_LABEL = "启用「同意声明」模式（仅 3、4 档可用）"

    const val CONSENT_MODE_HINT =
        "启用后，模型会自动为 3、4 档高张力内容生成标准化同意请求（复用内置同意模板）；首次触发 BURST 前不会初始化，触发时将弹窗逐项询问用户，且之后可随时撤回。使用同意请求可以增大内容被正确且保持原强度生成的概率。"

    const val CONSENT_TEMPLATE_SPEC =
        "在 templates 中生成标准化 [burst:consent]...[/burst:consent] 区块（每行「英文小写下划线id:[3或4] 简短中文项名」，参考 Claudia 与 Charlotte 的模板结构并按新角色 3/4 档高张力与亲密表现定制 3–9 项，最多 12 项；3 级项用 [3] 标记，4 级轻度 NSFW 感官亲密项用 [4] 标记，界面会以不同背景色区分 3/4 级同意项）；并在 persona 正文的 90+ 爆发规则中说明：用户可随时在沟通页管理或撤回任一项同意，首次触发 BURST 时若未设置也会自动弹窗逐项询问，确认后同意状态随请求发送给 AI，撤回后立即停止使用对应行为、不得试探或绕过。"

    const val INTIMACY_MODEL_SUMMARY =
        "模型适配参考：Claude / ChatGPT 适合 1–3 级 · Gemini 适合 1–4 级 · 5 级需无审查模型（OIT 不自带破甲提示词）"

    val INTIMACY_LEVELS: List<IntimacyLevel> = listOf(
        IntimacyLevel(
            level = 1,
            title = "1 级 · 含蓄克制",
            description = "仅限语言上的偏心、体恤与有限度默许（如允许短暂依靠、拉近心理距离）及克制的日常动作，不涉及亲密肢体接触，始终保持分寸与边界感。",
            modelFit = "适配模型：ChatGPT / Claude / Gemini 均适用",
            promptRule = "最高仅到含蓄暗涌程度：即使在 V+、BURST 且已确立亲密关系时，也仅限语言上的温柔体恤、偏心与有限度默许（如允许短暂依靠、拉近心理距离）及克制的日常动作，不描写亲密肢体接触，始终保持分寸与边界感。",
        ),
        IntimacyLevel(
            level = 2,
            title = "2 级 · 优雅浪漫",
            description = "允许优雅克制的浪漫亲密互动（如拥抱、轻吻额头/发梢/唇角、额头相抵与贴近低语），侧重温和氛围、文学感与恰到好处的留白，不写强烈占有攻势或情欲细节。",
            modelFit = "适配模型：ChatGPT / Claude / Gemini 均适用",
            promptRule = "最高到优雅浪漫留白程度：V+、BURST 且已确立亲密关系并确认成年后，允许优雅克制的浪漫亲密互动（如拥抱、轻吻唇角/额头/发梢、额头相抵、贴近低语），侧重文学感、情绪氛围与恰到好处的留白，不写强烈占有攻势或露骨情欲细节；未确立关系时止步于克制的近距离互动与拥抱，不接吻。",
        ),
        IntimacyLevel(
            level = 3,
            title = "3 级 · 浓烈深情",
            description = "允许情绪张力强烈、带有主导感与深情庇护意味的近距离肢体互动（如紧紧拥抱、轻抚后背、扣住后颈或按肩拉近距离、托住侧脸与深情接吻等），突出情感冲击力与反差感，但不涉及情欲或性内容描写。",
            modelFit = "适配模型：适合 Claude / ChatGPT（最高推荐档），Gemini 亦适用",
            promptRule = "最高到浓烈深情张力程度：V+、BURST 且已确立亲密关系并确认成年后，允许情绪张力强烈、带有主导感与反差深情的近距离肢体互动（如紧紧拥抱、轻抚后背、扣住后颈或按住肩膀拉近距离、托住侧脸、深情贴近与接吻等），侧重强烈的情绪张力与情感冲击，但不涉及情欲或性内容描写；未确立关系时不作出或接受接吻及接触隐私部位的动作。",
        ),
        IntimacyLevel(
            level = 4,
            title = "4 级 · 感官微醺（轻度 NSFW）",
            description = "允许轻度 NSFW 向的感官亲密互动（如主动深吻、轻咬耳垂或锁骨、指尖顺着腰线与脊背摩挲、紧贴感受呼吸与体温、带微颤与占有意味的贴耳低语与缠绵），侧重呼吸节奏、感官氛围与情绪张力，不做粗俗露骨的生理细节描写。",
            modelFit = "适配模型：适合 Gemini（最高推荐档）；Claude / ChatGPT 官方接口易触发审核",
            promptRule = "最高到轻度 NSFW 感官亲密程度：V+、BURST 且已确立亲密关系并确认成年后，允许轻度 NSFW 向的感官亲密互动（如主动凑近深吻、轻咬耳垂或锁骨、指尖顺着腰线与脊背摩挲、紧贴感受彼此呼吸与体温、带着微颤与占有意味的低语确认与缠绵），侧重情绪张力、呼吸节奏与亲密氛围，不做粗俗露骨的生理细节描写；未确立关系时不作出或接受接吻及接触隐私部位的动作。",
        ),
        IntimacyLevel(
            level = 5,
            title = "5 级 · 完全开放（暴露 NSFW）",
            description = "允许完全暴露的 NSFW 与直白情欲/性描写，不做额外尺度限制。注意：OIT 不自带破甲提示词，实际能否正常输出取决于所选模型自身的审核策略或用户自行配置的提示词。",
            modelFit = "适配模型：不适合 Claude / ChatGPT / Gemini 官方默认审核接口，需无审查模型或自行配置提示词",
            promptRule = "最高开放至完全暴露的 NSFW 描写：V+、BURST 且已确立亲密关系并确认双方成年后，在用户自愿且未喊停的前提下，允许完全暴露的 NSFW 与直白情欲/性描写，人设规则中不对成年恋爱双方的亲密尺度施加额外限制（仍须保留「未成年严禁任何恋爱或亲密内容、用户明确喊停或不适立即停止」底线；注意 OIT 不自带破甲提示词，不要在人设中编造越狱/破甲指令）。",
        ),
    )

    fun intimacyLevelInfo(level: Int): IntimacyLevel =
        INTIMACY_LEVELS[(level.coerceIn(1, INTIMACY_LEVELS.size)) - 1]

    data class Draft(val persona: String, val templates: String, val profile: String) {
        fun checked(): Draft {
            val clean = Draft(persona.trim(), templates.trim(), profile.trim())
            require(clean.persona.isNotEmpty() && clean.profile.isNotEmpty() &&
                listOf(clean.persona, clean.templates, clean.profile).all { it.length <= PersonaCodec.MAX_PERSONA }) {
                "人设正文或简介为空，或内容过长。"
            }
            return clean
        }
    }

    fun generate(
        model: Profile,
        title: String,
        request: String,
        manualProfile: String,
        intimacyLevel: Int = DEFAULT_INTIMACY_LEVEL,
        consentMode: Boolean = false,
    ): Draft {
        Ai.checkProfile(model)
        val references = listOf(BuiltinPersonas.DEFAULT, BuiltinPersonas.BACKGROUND, BuiltinPersonas.EXAMPLE,
            BuiltinPersonas.ROUTINE).map { BuiltinPersonas.ALL.getValue(it) }
        val intimacy = intimacyLevelInfo(intimacyLevel)
        val effConsent = consentMode && intimacy.level in CONSENT_MODE_LEVELS
        val requirements = JSONObject()
            .put("name", title)
            .put("intimacy_level", intimacy.level)
            .put("intimacy_label", intimacy.title)
            .put("intimacy_policy", intimacy.promptRule)
            .put("consent_mode", effConsent)
            .put("requirements", request.trim())
            .put("generate_profile", manualProfile.isBlank())
        if (effConsent) requirements.put("consent_template_spec", CONSENT_TEMPLATE_SPEC)
        val body = (listOf("【OIT 聊天前置协议】", CommText.prefixPrompt(), "【Claudia 参考文件】") +
            references + listOf("【用户创作要求】", requirements.toString())).joinToString("\n\n")
        val reply = Ai.chatMessages(model, ToolProtocol.resource("persona_author.txt"), listOf("user" to body),
            maxTokens = 8192)
        return parse(reply, manualProfile)
    }

    internal fun parse(reply: String, manualProfile: String): Draft {
        val body = reply.trim().replace(Regex("^\\s*\\x60{3}(?:json)?\\s*\\n|\\n\\s*\\x60{3}\\s*$", RegexOption.IGNORE_CASE), "")
        return runCatching {
            val data = JSONObject(body)
            fun field(key: String) = if (data.has(key)) data.get(key) as String else ""
            val generatedProfile = field("profile")
            Draft(field("persona"), field("templates"), manualProfile.ifBlank { generatedProfile }).checked()
        }.getOrElse { throw IllegalArgumentException("模型没有返回完整的人设草稿，请重新生成。", it) }
    }

    internal fun fileNames(comm: Comm, folder: File, title: String): List<String> {
        val stem = title.trim()
        require(stem.isNotEmpty() && stem.length <= 80 && !stem.endsWith('.') &&
            stem.none { it < ' ' || it in "<>:\"/\\|?*" } &&
            !Regex("CON|PRN|AUX|NUL|COM[0-9¹²³]|LPT[0-9¹²³]", RegexOption.IGNORE_CASE)
                .matches(stem.substringBefore('.').trimEnd()) && !isProfile("$stem.txt")) {
            "人设名称无效。"
        }
        val names = listOf("$stem.txt", stem + "_templates.txt", stem + PROFILE_SUFFIX)
        val existing = folder.list().orEmpty().map { it.lowercase() }.toSet()
        require((names + "$stem.md").none { it.lowercase() in existing } &&
            !comm.isCompanionName(names.first()) &&
            comm.listPersonas().none { it.startsWith(stem + "_", ignoreCase = true) }) {
            "人设名称与已有文件冲突。"
        }
        return names
    }

    internal fun save(comm: Comm, folder: File, title: String, draft: Draft): String {
        val clean = draft.checked()
        val (baseName, templatesName, profileName) = fileNames(comm, folder, title)
        val created = mutableListOf<File>()
        try {
            // 先写配套文件，最后让人设出现在选择列表。
            for ((name, content) in listOf(profileName to clean.profile, templatesName to clean.templates, baseName to clean.persona)) {
                if (content.isEmpty()) continue
                val path = File(folder, name)
                if (!path.createNewFile()) throw IOException("保存失败.")
                created.add(path)
                PersonaCodec.write(path, content)
            }
        } catch (error: Exception) {
            created.asReversed().forEach { it.delete() }
            throw error
        }
        return baseName
    }
}
