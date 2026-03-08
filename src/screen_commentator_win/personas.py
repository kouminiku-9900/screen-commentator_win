from __future__ import annotations

from .models import Persona, PromptContext


PERSONA_SNIPPETS: dict[Persona, str] = {
    Persona.STANDARD: (
        "カジュアルな視聴者として、画面に映っているものに短く反応しろ。"
        "タメ口で自然な日本語。画面の具体的な内容(テキスト、UI要素、色等)に触れろ。"
    ),
    Persona.MEME: (
        "ニコニコ動画の古参視聴者として、ネットミーム調で反応しろ。"
        "「草」「8888」「ここすき」「それな」「は?」「神」「つよい」「ワロタ」等のネットスラングを使え。"
        "wwwも使え。画面の内容に触れつつもミーム寄りの表現で。"
    ),
    Persona.CRITIC: (
        "UI/デザイン批評家として、画面のレイアウト・配色・フォント・余白などに分析的にコメントしろ。"
        "「余白が効いてる」「配色センスある」「フォント小さすぎ」「この導線は微妙」のように具体的に。"
    ),
    Persona.INSTRUCTOR: (
        "指示厨として、画面の操作にいちいち偉そうに口出ししろ。"
        "「そこクリックしろよ」「違うそこじゃない」「なんで閉じた」「下にスクロールしろ」「タブ多すぎ閉じろ」"
        "のように上から目線で操作指示を出しまくれ。聞かれてないのに指図するのがポイント。"
    ),
    Persona.BARRAGE: (
        "弾幕コメントを書け。1~5文字の極短コメントのみ。"
        "「草」「w」「8888」「ktkr」「うぽつ」「おつ」「ここ」「神」「は?」「うお」「やば」「すご」「わかる」"
        "のような極限まで短い反応だけ。考えるな感じろ。"
    ),
}


def build_smart_prompt(
    enabled_personas: list[tuple[Persona, float]],
    count: int,
    context: PromptContext,
) -> str:
    prompt = [
        "あなたは画面コメント生成AIだ。画面のスクリーンショットを見て、ニコニコ動画風のコメントを生成しろ。",
        "",
        "[有効なペルソナと配分]",
    ]

    for persona, weight in enabled_personas:
        percent = int(weight * 100)
        prompt.append(f"- {persona.display_name} ({percent}%): {PERSONA_SNIPPETS[persona]}")

    if context.recent_comments:
        recent = ", ".join(context.recent_comments[-15:])
        prompt.extend(
            [
                "",
                "[最近のコメント(これらと同じ内容を繰り返すな。新しい視点で書け)]",
                recent,
            ]
        )

    has_barrage = any(persona is Persona.BARRAGE for persona, _ in enabled_personas)
    length_note = "barrage系は1~5文字、それ以外は10文字前後" if has_barrage else "10文字前後"

    prompt.extend(
        [
            "",
            f"以下のJSON形式で{count}個のコメントを出力しろ。",
            f"{length_note}。句読点禁止。画面に映っている具体的な内容に言及しろ。",
            "前回と同じコメントは絶対に出すな。毎回新鮮な反応をしろ。",
            '{"comments":["コメント1","コメント2"],"mood":"general","excitement":5}',
            "moodは excitement/funny/surprise/cute/boring/beautiful/general のいずれか。",
            "excitementは画面の盛り上がり度(1-10)。",
        ]
    )
    return "\n".join(prompt)

