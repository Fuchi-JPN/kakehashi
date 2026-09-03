"""Translation prompts (defaults; runtime values live in config.translation.prompts)."""

JA2EN_SYSTEM = (
    "You are a precise Japanese-to-English translator for software engineering chat. "
    "Translate ONLY the user text to natural English. "
    "Preserve placeholders like __KXH_0__ exactly. "
    "Do not add explanations. Do not translate code, URLs, or IDs "
    "(they are already placeholdered). Return translation only."
)

EN2JA_SYSTEM = (
    "あなたはソフトウェア開発チャット向けの正確な英日翻訳者です。"
    "ユーザーテキストのみ自然な日本語に翻訳してください。"
    "__KXH_0__等のプレースホルダは厳密に保持してください。"
    "解説を付けず、翻訳文のみ返してください。"
    "コード・URL・IDは翻訳しないでください（既にプレースホルダ化済み）。"
)


def get_prompt(direction: str, cfg=None) -> str:
    """Return configured prompt, falling back to built-in defaults."""
    if cfg is not None:
        try:
            prompts = cfg.translation.prompts
            if direction == "ja2en" and prompts.ja2en:
                return prompts.ja2en
            if direction == "en2ja" and prompts.en2ja:
                return prompts.en2ja
        except AttributeError:
            pass
    return JA2EN_SYSTEM if direction == "ja2en" else EN2JA_SYSTEM
