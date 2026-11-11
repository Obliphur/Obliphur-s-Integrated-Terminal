# user-said-now-means-change-now

来源：Claude 项目记忆 user-now-means-change.md，已转换为 Codex 项目说明。

When the user says "现在…" describing how a feature works, they mean the code should be modified so it works that way — it's an instruction to implement the change, not a question to be verified or discussed.

**Why:** After I summarized the pre-start (提前启动) network-exempt behavior with its actual code semantics, the user clarified "以后,我说'现在'就意味着要改成我描述的样子" — they were stating a desired behavior and expected it to be implemented.

**How to apply:** In this project, treat "现在 X 会 Y" statements as change requests: implement so that X does Y as described, without first treating it as a question to verify. (Still check whether the description is unambiguous enough; if not, ask before coding.)
