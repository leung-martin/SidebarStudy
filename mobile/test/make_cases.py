"""Writes grading cases + the PC app's verdicts to test/cases.json, for the JS port to match."""
import glob, json, os, random, sys, types

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
import app  # noqa: E402

cards = [c for f in sorted(glob.glob(os.path.join(ROOT, "flashcards", "*.json")))
         for c in json.load(open(f, encoding="utf-8"))]
by_simp = {}
for c in cards:
    by_simp.setdefault(c["simplified"], c)

def card(simp, trad, py):
    return {"code": "T", "simplified": simp, "traditional": trad, "pinyin": py, "english": ""}

manual = [
    (card("办理", "辦理", "bàn lǐ"), ["办理", "半里", "板栗", "帮你", "我要办理入住"]),
    (card("你好", "你好", "nǐ hǎo"), ["您好"]),
    (card("你好，打扰一下。", "你好，打擾一下。", "nǐ hǎo, dǎ rǎo yí xià"), ["您好打扰一下", "你好打扰一下"]),
    (card("我想去北京旅游", "我想去北京旅遊", "wǒ xiǎng qù běi jīng lǚ yóu"),
     ["我想去北京旅游", "我想去南京旅游", "我要去北京", "他", "你好我想去南京旅行"]),
    (card("去哪儿", "去哪兒", "qù nǎr"), ["你想去哪儿", "去那儿", "去哪", "去"]),
    (card("手机APP", "手機APP", "shǒu jī APP"), ["手机", "我的手机"]),
    (card("你觉得John明天会来吗？", "你覺得John明天會來嗎？", "nǐ jué de John míng tiān huì lái ma"),
     ["你觉得约翰明天会来吗", "你觉得张明天会来吗", "你觉得明天会来吗", "你觉得约翰今天会来吗", "你觉得一二三四五六明天会来吗"]),
    (card("家", "家", "jiā"), ["教", "就"]),
    (card("大家", "大家", "dà jiā"), ["大概", "大胶"]),
    (card("下", "下", "xià"), ["笑"]),
    (card("我们试试。", "我們試試。", "wǒ men shì shi"), ["我们试试", "我们是是", "我们试事"]),
    (card("办事(儿)", "辦事(兒)", "bàn shì(r)"), ["把是", "办事儿"]),
    (card("我的儿子", "我的兒子", "wǒ de ér zi"), ["我的子", "我的儿子"]),
    (card("等一下", "等一下", "děng yí xià"), ["等一下", "等一夏"]),
    (card("喂", "喂", "wèi / wéi"), ["喂", "为"]),
]
random.seed(7)
real = [(c, [c["simplified"]]) for c in random.sample(cards, 400)]
cases = []
for c, heards in manual + real:
    for h in heards:
        a = types.SimpleNamespace(card=c, strict_var=None)
        for m in ("_check_whole", "_only_latin_gaps"):
            setattr(a, m, getattr(app.App, m).__get__(a))
        ok, py, tone_ok, matched, ruby, tone_off = app.App._check(a, h)
        cases.append({"card": c, "heard": h, "manual": c["code"] == "T",
                      "want": {"ok": bool(ok), "toneOk": bool(tone_ok), "matched": sorted(matched),
                               "toneOff": sorted(tone_off), "ruby": {str(k): v for k, v in sorted(ruby.items())}}})
json.dump(cases, open(os.path.join(os.path.dirname(__file__), "cases.json"), "w", encoding="utf-8"), ensure_ascii=False)
print(len(cases), "cases")
