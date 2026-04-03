"""Ollama（ローカルLLM）クライアント

全てのLLM呼び出しはこのモジュールを経由する。
Ollama未起動時は静かにフォールバックする。
"""

import requests
import json

OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "llama3"


def is_available() -> bool:
    """Ollamaが起動しているか確認する。"""
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


def generate(prompt: str, model: str = DEFAULT_MODEL, temperature: float = 0.3) -> str | None:
    """テキストを生成する。

    Args:
        prompt: プロンプト
        model: モデル名
        temperature: 低いほど事実に忠実（0.3推奨）

    Returns:
        生成されたテキスト。Ollama未起動時はNone。
    """
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": temperature},
            },
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.json().get("response")
    except Exception:
        pass
    return None


def analyze_text(text: str, instruction: str, model: str = DEFAULT_MODEL) -> str | None:
    """テキストを分析する。

    Args:
        text: 分析対象のテキスト
        instruction: 分析指示

    Returns:
        分析結果。
    """
    prompt = f"""{instruction}

対象テキスト:
{text}

ルール:
- テキストに書かれている事実のみを抽出すること
- 推測、予測、主観は一切含めないこと
- 数値があれば必ず含めること
"""
    return generate(prompt, model=model, temperature=0.1)


def extract_themes_from_text(text: str) -> list[str]:
    """テキストからテーマキーワードを抽出する。

    Ollama未起動時は空リストを返す。
    """
    result = generate(
        f"""以下のテキストから投資テーマに関するキーワードを抽出してください。
JSON配列で返してください。例: ["AI電力", "データセンター"]

テキスト:
{text}

キーワードのみをJSON配列で出力:""",
        temperature=0.1,
    )

    if not result:
        return []

    try:
        # JSONを抽出
        start = result.find("[")
        end = result.rfind("]") + 1
        if start >= 0 and end > start:
            return json.loads(result[start:end])
    except (json.JSONDecodeError, ValueError):
        pass

    return []
