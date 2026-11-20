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

    fun generate(model: Profile, title: String, request: String, manualProfile: String): Draft {
        Ai.checkProfile(model)
        val references = listOf(BuiltinPersonas.DEFAULT, BuiltinPersonas.BACKGROUND, BuiltinPersonas.EXAMPLE,
            BuiltinPersonas.ROUTINE).map { BuiltinPersonas.ALL.getValue(it) }
        val requirements = JSONObject().put("name", title).put("requirements", request.trim())
            .put("generate_profile", manualProfile.isBlank())
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
