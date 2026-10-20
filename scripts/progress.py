#!/usr/bin/env python3
"""출제 순서와 승급/강등을 계산하는 스케줄러. 모델은 이 스크립트가 고른 대로만 출제한다.

과목 폴더에서 실행:
    python <SKILL_DIR>/scripts/progress.py init
    python <SKILL_DIR>/scripts/progress.py mode [normal|cram] [--until "2026-10-22 09:00"]
    python <SKILL_DIR>/scripts/progress.py pick [--batch 5] [--lecture 3,5] [--core] [--weak] [--final]
    python <SKILL_DIR>/scripts/progress.py record <ID[,ID...]> <result> --asked <level> [--note "..."] [--score N]
    python <SKILL_DIR>/scripts/progress.py undo
    python <SKILL_DIR>/scripts/progress.py final
    python <SKILL_DIR>/scripts/progress.py summary [--lecture N] [--core]
    python <SKILL_DIR>/scripts/progress.py stats [--json]

단계(level): 0=객관식(mc), 1=단답식(short), 2=서술형(essay), 3=숙련(mastered)
모드: normal = 일반 모드(기본), cram = "I'm fucked. 나는 좆됐어." 모드(벼락치기)
결과(result):
    correct  정답 (객관식은 '확실'로 맞힘)
    unsure   '애매'로 맞힘. 일반 모드에선 정답과 같고, 좆됐어 모드에선 승급하지 않음
    guess    '찍음'으로 맞힘 -> 정답으로 치지 않음
    hint     힌트를 보고 맞힘 -> 정답으로 치지 않음
    partial  부분 정답 (서술형 40~69점, 단답 철자 오류 등)
    wrong    오답 또는 '패스'
"""
import argparse
import json
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

LEVEL_NAMES = ["mc", "short", "essay", "mastered"]
LEVEL_KO = ["객관식", "단답식", "서술형", "숙련"]
CRAM_LABEL = "I'm fucked. 나는 좆됐어."

# 모드별 설정. 숫자를 바꿔서 조정한다.
MODES = {
    "normal": {
        "label": "일반",
        "promote": {0: 2, 1: 2, 2: 1},   # 해당 단계에서 몇 번 연속 맞히면 승급
        "mastered_weight": 0.12,          # 숙련 개념 출제 가중치 (일반 개념은 1 이상)
        "combo_prob": 0.05,               # 복합 문제 확률
        "angle_prob": 0.5,                # 숙련 복습 중 '다각도' 비율
        "essay_inject": 0.0,              # 중요 개념을 낮은 단계에서 서술형으로 낼 확률
        "unsure_promotes": True,          # '애매' 정답도 승급에 포함하는지
        "diagnostic": False,              # 처음 보는 개념부터 한 바퀴 (진단 라운드)
        "weight_power": 1,                # 중요도(weight)를 얼마나 강하게 반영할지
        "unseen_boost": 1.6,
        "due": {"wrong": (2, 4), "soft": (2, 5), "correct": (3, 7), "promoted": (2, 4), "mastered": (15, 25)},
    },
    "cram": {
        "label": CRAM_LABEL,
        "promote": {0: 1, 1: 1, 2: 1},
        "mastered_weight": 0.03,
        "combo_prob": 0.0,
        "angle_prob": 0.0,
        "essay_inject": 0.15,
        "unsure_promotes": False,
        "diagnostic": True,
        "weight_power": 2,
        "unseen_boost": 2.5,
        "due": {"wrong": (1, 3), "soft": (2, 4), "correct": (3, 6), "promoted": (1, 3), "mastered": (40, 60)},
    },
}
COMBO_MIN_POOL = 4          # 서술형 이상 개념이 이만큼 있어야 복합 문제 가능
RECENT_EXCLUDE = 2          # 직전 N개 출제 개념은 바로 다시 내지 않음
FINAL_CHECK_HOURS = 2       # 시험 몇 시간 전에 마지막 점검을 제안할지
CORE_WEIGHT = 2             # 이 weight 이상이 '핵심' 개념
ESSAY_BACKLOG = 15          # 서술형 단계 개념이 이만큼 쌓이면 묶음 뒤 서술형을 2개로

VARIANTS = {
    "definition": {
        "mc": ["def_to_term", "term_to_def", "example_to_concept", "not_true"],
        "short": ["write_term", "fill_blank", "write_definition"],
        "essay": ["compare", "explain_with_example"],
    },
    "principle": {
        "mc": ["correct_statement", "not_true", "cause_effect", "order"],
        "short": ["key_term", "order", "fill_blank"],
        "essay": ["explain_why", "predict_and_explain", "compare"],
    },
    "formula": {
        "mc": ["compute_choose", "formula_choose", "interpret"],
        "short": ["compute", "write_formula"],
        "essay": ["full_solution", "derive_or_prove"],
    },
    "code": {
        "mc": ["output_choose", "bug_line", "complexity_choose", "fill_blank_choose"],
        "short": ["write_output", "fill_blank_line", "write_complexity"],
        "essay": ["write_function", "explain_code"],
    },
}


# ---------------------------------------------------------------- 공통
def paths(root):
    d = Path(root) / ".drill"
    return d / "concepts.json", d / "progress.json", d / "log.jsonl"


def load_json(p, default=None):
    if not p.exists():
        if default is not None:
            return default
        print(f"[오류] {p} 가 없습니다. 먼저 setup을 진행하세요.")
        sys.exit(1)
    return json.loads(p.read_text(encoding="utf-8"))


def save_json(p, data):
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def new_state():
    return {"level": 0, "streak": 0, "due": 0, "seen": 0, "correct": 0, "lapses": 0,
            "reviews": 0, "last_turn": None, "recent": []}


def settings_of(prog):
    s = prog.setdefault("settings", {})
    s.setdefault("mode", "normal")
    s.setdefault("exam_at", None)
    s.setdefault("final_start_turn", None)
    return s


def params_of(prog):
    return MODES[settings_of(prog)["mode"]]


def norm_lecture(x):
    s = str(x).strip()
    return str(int(s)) if s.isdigit() else s.lower()


def lec_key(x):
    return norm_lecture(x).zfill(4)


def rint(rng):
    return random.randint(*rng)


def hours_left(settings):
    if not settings.get("exam_at"):
        return None
    return (datetime.fromisoformat(settings["exam_at"]) - datetime.now()).total_seconds() / 3600


def parse_when(text):
    text = text.strip()
    now = datetime.now()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    for fmt in ("%m-%d %H:%M", "%m/%d %H:%M"):
        try:
            d = datetime.strptime(text, fmt).replace(year=now.year)
            return d if d > now else d.replace(year=now.year + 1)
        except ValueError:
            pass
    print('[오류] 시각 형식: "2026-10-22 09:00" 또는 "10-22 09:00"')
    sys.exit(1)


# ---------------------------------------------------------------- init / mode
def cmd_init(args):
    cpath, ppath, _ = paths(args.root)
    items = load_json(cpath).get("concepts", [])
    ids, errors = set(), []
    for c in items:
        for key in ("id", "lecture", "title", "type", "source", "key_points"):
            if key not in c:
                errors.append(f"{c.get('id', '?')}: '{key}' 없음")
        if c.get("id") in ids:
            errors.append(f"중복 id: {c['id']}")
        ids.add(c.get("id"))
        if c.get("type") not in VARIANTS:
            errors.append(f"{c.get('id')}: type '{c.get('type')}' 는 {list(VARIANTS)} 중 하나여야 함")
    if errors:
        print("[concepts.json 오류]\n- " + "\n- ".join(errors))
        sys.exit(1)

    prog = load_json(ppath, default={"turn": 0, "recent_ids": [], "concepts": {}})
    settings_of(prog)
    added = [i for i in ids if i not in prog["concepts"]]
    for i in added:
        prog["concepts"][i] = new_state()
    removed = [i for i in list(prog["concepts"]) if i not in ids]
    for i in removed:
        del prog["concepts"][i]
    save_json(ppath, prog)

    by_lec = {}
    core = 0
    for c in items:
        by_lec[str(c["lecture"])] = by_lec.get(str(c["lecture"]), 0) + 1
        core += c.get("weight", 1) >= CORE_WEIGHT
    print(f"개념 {len(items)}개 (추가 {len(added)}, 삭제 {len(removed)}) · 핵심 {core}개")
    print("강의별: " + ", ".join(f"{k}강 {v}개" for k, v in sorted(by_lec.items(), key=lambda kv: lec_key(kv[0]))))


def cmd_mode(args):
    _, ppath, _ = paths(args.root)
    prog = load_json(ppath)
    st = settings_of(prog)
    if args.name:
        st["mode"] = args.name
    if args.until:
        st["exam_at"] = parse_when(args.until).isoformat(timespec="minutes")
    if args.clear_until:
        st["exam_at"] = None
    save_json(ppath, prog)
    h = hours_left(st)
    exam = f" · 시험까지 {h:.1f}시간 ({st['exam_at']})" if h is not None else ""
    print(f"현재 모드: {MODES[st['mode']]['label']} 모드{exam}")


# ---------------------------------------------------------------- pick
def weight_of(c, s, turn, P):
    if s["level"] >= 3:
        idle = turn - (s["last_turn"] or 0)
        return P["mastered_weight"] * (1 + idle / 50)
    w = float(c.get("weight", 1)) ** P["weight_power"]
    w *= 1 + 0.5 * min(s["lapses"], 4)
    if s["seen"] == 0:
        w *= P["unseen_boost"]
    return w


def weighted_order(ids, weights):
    """가중치 비복원 추출 순서 (Efraimidis-Spirakis)."""
    keyed = [(random.random() ** (1.0 / max(w, 1e-9)), i) for i, w in zip(ids, weights)]
    return [i for _, i in sorted(keyed, reverse=True)]


def concept_view(c, s):
    return {
        "id": c["id"], "lecture": c["lecture"], "title": c["title"], "type": c["type"],
        "source": c["source"], "key_points": c["key_points"],
        "misconceptions": c.get("misconceptions", []), "related": c.get("related", []),
        "language": c.get("language"), "weight": c.get("weight", 1),
        "history": {"seen": s["seen"], "correct": s["correct"], "lapses": s["lapses"],
                    "streak": s["streak"], "recent_questions": s["recent"]},
    }


def make_item(cid, concepts, st, P, diagnostic=False, final=False):
    c, s = concepts[cid], st[cid]
    level, flags = s["level"], {}
    if final:
        lname, variant, flags["final_check"] = "mastered", "final_check", True
    elif level >= 3:
        lname = "mastered"
        variant = "angle" if random.random() < P["angle_prob"] else "review_short"
    elif diagnostic:
        lname, variant, flags["diagnostic"] = "mc", random.choice(VARIANTS[c["type"]]["mc"]), True
    elif level < 2 and c.get("weight", 1) >= CORE_WEIGHT and random.random() < P["essay_inject"]:
        lname, variant, flags["injected"] = "essay", random.choice(VARIANTS[c["type"]]["essay"]), True
    else:
        lname = LEVEL_NAMES[level]
        variant = random.choice(VARIANTS[c["type"]][lname])
    item = {"level": lname, "level_ko": LEVEL_KO[LEVEL_NAMES.index(lname)], "variant": variant, **flags,
            "concepts": [concept_view(c, s)]}
    if lname == "mc":
        item["mc_answer_position"] = random.randint(1, 4)
    return item


def combo_item(pool, concepts, st):
    advanced = [i for i in pool if st[i]["level"] >= 2]
    k = 3 if len(advanced) >= 8 and random.random() < 0.3 else 2
    shuffled = random.sample(advanced, len(advanced))
    chosen, lectures = [], set()
    for i in shuffled:  # 가능하면 서로 다른 강의에서
        lec = norm_lecture(concepts[i]["lecture"])
        if lec not in lectures:
            chosen.append(i)
            lectures.add(lec)
        if len(chosen) == k:
            break
    for i in shuffled:
        if len(chosen) == k:
            break
        if i not in chosen:
            chosen.append(i)
    return {"level": "combo", "level_ko": "복합", "variant": "combo",
            "concepts": [concept_view(concepts[i], st[i]) for i in chosen]}


def build_pool(args, concepts, prog, notices):
    st = prog["concepts"]
    pool = [i for i in concepts if i in st]
    if args.lecture:
        wanted = {norm_lecture(x) for x in args.lecture.split(",")}
        pool = [i for i in pool if norm_lecture(concepts[i]["lecture"]) in wanted]
    if args.core:
        core = [i for i in pool if concepts[i].get("weight", 1) >= CORE_WEIGHT]
        if core:
            pool = core
        else:
            notices.append("핵심 개념(weight 2)이 없어서 전체에서 출제합니다.")
    if args.weak:
        weak = [i for i in pool if st[i]["lapses"] > 0 and st[i]["level"] < 3]
        if weak:
            pool = weak
        else:
            notices.append("약점 개념이 없어서 전체에서 출제합니다.")
    return pool


def cmd_pick(args):
    cpath, ppath, _ = paths(args.root)
    concepts = {c["id"]: c for c in load_json(cpath)["concepts"]}
    prog = load_json(ppath)
    S, P = settings_of(prog), params_of(prog)
    st = prog["concepts"]
    turn = prog["turn"] + 1
    notices = []

    h = hours_left(S)
    if h is not None:
        if h <= 0:
            notices.append("설정된 시험 시각이 지났습니다. 'mode --clear-until'로 지우거나 새 시각을 설정하세요.")
        elif S["mode"] == "cram" and h <= FINAL_CHECK_HOURS and S["final_start_turn"] is None and not args.final \
                and any(s["level"] == 3 for s in st.values()):
            notices.append(f"시험까지 {h:.1f}시간. 마지막 점검(숙련 개념 단답식 한 바퀴)을 제안하세요.")

    pool = build_pool(args, concepts, prog, notices)
    base = {"question_no": turn, "mode": S["mode"], "mode_label": P["label"]}

    # 마지막 점검: 점검 시작 이후 아직 안 물어본 숙련 개념만
    if args.final:
        if S["final_start_turn"] is None:
            S["final_start_turn"] = prog["turn"]
            save_json(ppath, prog)
        fst = S["final_start_turn"]
        todo = [i for i in pool if st[i]["level"] == 3 and (st[i]["last_turn"] or 0) <= fst]
        if not todo:
            return out({**base, "final_done": True, "notices": notices +
                        ["마지막 점검 완료. 강등된 개념은 일반 출제로 다시 나옵니다."]})
        order = random.sample(todo, len(todo))[: (args.batch or 1)]
        items = [make_item(i, concepts, st, P, final=True) for i in order]
        return out({**base, "final_remaining": len(todo), "batch": items, "then": [], "notices": notices}
                   if args.batch else {**base, "final_remaining": len(todo), **items[0], "notices": notices})

    if not pool:
        return out({**base, "error": "출제할 개념이 없습니다. --lecture 값을 확인하세요."})

    recent = set(prog.get("recent_ids", [])[-RECENT_EXCLUDE:])

    # 진단 라운드: 좆됐어 모드에서는 처음 보는 개념을 먼저 한 바퀴
    unseen = [i for i in pool if st[i]["seen"] == 0]
    diagnostic = P["diagnostic"] and not args.weak and bool(unseen)
    if diagnostic:
        w = [float(concepts[i].get("weight", 1)) ** P["weight_power"] for i in unseen]
        order = weighted_order(unseen, w)
        notices.append(f"진단 라운드: 처음 보는 개념 {len(unseen)}개 남음")
    else:
        due = [i for i in pool if st[i]["due"] <= turn and i not in recent]
        later = sorted((i for i in pool if i not in due and i not in recent), key=lambda i: st[i]["due"])
        order = weighted_order(due, [weight_of(concepts[i], st[i], turn, P) for i in due]) + later
        if not order:
            order = list(pool)

    # 한 문제
    if not args.batch:
        advanced = [i for i in pool if st[i]["level"] >= 2]
        if not diagnostic and not args.weak and len(advanced) >= COMBO_MIN_POOL and random.random() < P["combo_prob"]:
            return out({**base, **combo_item(pool, concepts, st), "notices": notices})
        return out({**base, **make_item(order[0], concepts, st, P, diagnostic=diagnostic), "notices": notices})

    # 묶음: 객관식·단답식만 묶고, 서술형급 문제는 'then'에 따로 (보통 1개, 서술형이 많이 밀려 있으면 2개)
    essay_backlog = sum(1 for i in pool if st[i]["level"] == 2)
    max_then = 2 if essay_backlog >= ESSAY_BACKLOG else 1
    items, then = [], []
    essay_like = lambda it: it["level"] in ("essay", "combo") or it["variant"] == "angle"
    for cid in order:
        it = make_item(cid, concepts, st, P, diagnostic=diagnostic)
        if essay_like(it):
            if len(then) < max_then:
                then.append(it)
            continue
        if len(items) < args.batch:
            items.append(it)
        if len(items) >= args.batch and len(then) >= max_then:
            break
    if not then and not diagnostic:
        used = {x["concepts"][0]["id"] for x in items}
        due_essay = [i for i in pool if st[i]["level"] == 2 and st[i]["due"] <= turn and i not in used]
        if due_essay:
            then.append(make_item(random.choice(due_essay), concepts, st, P))
    return out({**base, "batch": items, "then": then, "notices": notices})


def out(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------- record
def apply(s, result, turn, note, P, asked):
    s["seen"] += 1
    s["last_turn"] = turn
    before = s["level"]
    D = P["due"]
    if result == "unsure" and P["unsure_promotes"]:
        result = "correct"
    injected = asked == "essay" and s["level"] < 2

    if injected:  # 낮은 단계에서 서술형을 맞히면 바로 숙련
        if result == "correct":
            s["correct"] += 1
            s["level"], s["streak"], s["reviews"] = 3, 0, 0
            s["due"] = turn + rint(D["mastered"])
        elif result == "wrong":
            s["lapses"] += 1
            s["streak"] = 0
            s["due"] = turn + rint(D["wrong"])
        else:
            s["correct"] += result == "unsure"
            s["streak"] = 0
            s["due"] = turn + rint(D["soft"])
    elif result == "correct":
        s["correct"] += 1
        s["streak"] += 1
        lvl = s["level"]
        if lvl < 3 and s["streak"] >= P["promote"][lvl]:
            s["level"], s["streak"] = lvl + 1, 0
            if s["level"] == 3:
                s["reviews"] = 0
                s["due"] = turn + rint(D["mastered"])
            else:
                s["due"] = turn + rint(D["promoted"])
        elif lvl == 3:
            s["reviews"] += 1
            lo, hi = D["mastered"]
            s["due"] = turn + lo * 2 ** min(s["reviews"], 3) + random.randint(0, hi - lo)
        else:
            s["due"] = turn + rint(D["correct"])
    elif result in ("unsure", "partial", "guess", "hint"):
        s["correct"] += result == "unsure"
        s["streak"] = 0
        if s["level"] == 3:
            s["level"] = 2
        s["due"] = turn + rint(D["soft"])
    elif result == "wrong":
        s["lapses"] += 1
        s["streak"] = 0
        s["level"] = max(0, s["level"] - 1)
        s["due"] = turn + rint(D["wrong"])
    if note:
        s["recent"] = (s["recent"] + [note])[-5:]
    return before, s["level"]


def cmd_record(args):
    _, ppath, lpath = paths(args.root)
    prog = load_json(ppath)
    P = params_of(prog)
    ids = [x.strip() for x in args.ids.split(",") if x.strip()]
    missing = [i for i in ids if i not in prog["concepts"]]
    if missing:
        print(f"[오류] 없는 개념 id: {missing}")
        sys.exit(1)
    turn = prog["turn"] + 1
    snapshot = {"turn": prog["turn"], "recent_ids": list(prog.get("recent_ids", [])),
                "concepts": {i: json.loads(json.dumps(prog["concepts"][i])) for i in ids}}
    changes = []
    for i in ids:
        b, a = apply(prog["concepts"][i], args.result, turn, args.note, P, args.asked)
        if b != a:
            changes.append(f"{i}: {LEVEL_KO[b]} → {LEVEL_KO[a]} ({'승급' if a > b else '강등'})")
    prog["turn"] = turn
    prog["recent_ids"] = (prog.get("recent_ids", []) + ids)[-10:]
    save_json(ppath, prog)
    with lpath.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"turn": turn, "ts": datetime.now().isoformat(timespec="seconds"),
                            "mode": settings_of(prog)["mode"], "ids": ids, "result": args.result,
                            "asked": args.asked, "score": args.score, "note": args.note,
                            "snapshot": snapshot}, ensure_ascii=False) + "\n")
    print(json.dumps({"recorded": turn, "result": args.result, "level_changes": changes}, ensure_ascii=False))


def cmd_undo(args):
    _, ppath, lpath = paths(args.root)
    if not lpath.exists() or not lpath.read_text(encoding="utf-8").strip():
        print("되돌릴 기록이 없습니다.")
        return
    lines = lpath.read_text(encoding="utf-8").strip().split("\n")
    last = json.loads(lines[-1])
    prog = load_json(ppath)
    snap = last["snapshot"]
    prog["turn"] = snap["turn"]
    prog["recent_ids"] = snap["recent_ids"]
    for i, s in snap["concepts"].items():
        prog["concepts"][i] = s
    save_json(ppath, prog)
    lpath.write_text("\n".join(lines[:-1]) + ("\n" if len(lines) > 1 else ""), encoding="utf-8")
    print(f"{last['turn']}번 기록({', '.join(last['ids'])}: {last['result']})을 되돌렸습니다.")


def cmd_final(args):
    _, ppath, _ = paths(args.root)
    prog = load_json(ppath)
    S = settings_of(prog)
    S["final_start_turn"] = prog["turn"]
    save_json(ppath, prog)
    n = sum(1 for s in prog["concepts"].values() if s["level"] == 3)
    print(f"마지막 점검 시작: 숙련 개념 {n}개를 한 번씩 확인합니다. (pick --final)")


# ---------------------------------------------------------------- summary
def cmd_summary(args):
    """요약본 뼈대. 모델은 이 뼈대 위에 노트를 참고해 설명을 덧붙여 .drill/summary.md에 저장한다."""
    cpath, ppath, _ = paths(args.root)
    concepts = load_json(cpath)["concepts"]
    st = load_json(ppath, default={"concepts": {}})["concepts"]
    lectures = sorted({str(c["lecture"]) for c in concepts}, key=lec_key)
    if not args.lecture:
        print("요약 순서: " + ", ".join(
            f"{l}강({sum(1 for c in concepts if str(c['lecture']) == l)}개)" for l in lectures))
        return
    lec = norm_lecture(args.lecture)
    items = [c for c in concepts if norm_lecture(c["lecture"]) == lec]
    if args.core:
        items = [c for c in items if c.get("weight", 1) >= CORE_WEIGHT] or items
    if not items:
        print(f"{args.lecture}강 개념이 없습니다.")
        return
    nxt = [l for l in lectures if lec_key(l) > lec_key(lec)]
    print(f"## {args.lecture}강 ({len(items)}개 개념)" + (f"  · 다음: {nxt[0]}강" if nxt else "  · 마지막 강의"))
    for c in items:
        star = " ★" if c.get("weight", 1) >= CORE_WEIGHT else ""
        lvl = st.get(c["id"], {}).get("level")
        tag = f" `{LEVEL_KO[lvl]}`" if lvl else ""
        print(f"\n### {c['title']}{star}{tag}  ({c['source']})")
        for k in c["key_points"]:
            print(f"- {k}")
        if c.get("misconceptions"):
            print("- 헷갈림 주의: " + "; ".join(c["misconceptions"]))


# ---------------------------------------------------------------- stats
def pace(lpath, window_h=2.0):
    if not lpath.exists():
        return None
    now = datetime.now()
    times = []
    for line in lpath.read_text(encoding="utf-8").strip().split("\n"):
        if not line:
            continue
        ts = json.loads(line).get("ts")
        if ts:
            t = datetime.fromisoformat(ts)
            if now - t <= timedelta(hours=window_h):
                times.append(t)
    if len(times) < 5:
        return None
    span = max((now - min(times)).total_seconds() / 3600, 0.25)
    return len(times) / span


def cmd_stats(args):
    cpath, ppath, lpath = paths(args.root)
    concepts = {c["id"]: c for c in load_json(cpath)["concepts"]}
    prog = load_json(ppath)
    S, P = settings_of(prog), params_of(prog)
    st = {i: s for i, s in prog["concepts"].items() if i in concepts}

    lectures = {}
    for i, s in st.items():
        lec = str(concepts[i]["lecture"])
        d = lectures.setdefault(lec, {"total": 0, "levels": [0, 0, 0, 0], "unseen": 0, "seen": 0, "correct": 0})
        d["total"] += 1
        d["levels"][s["level"]] += 1
        d["unseen"] += s["seen"] == 0
        d["seen"] += s["seen"]
        d["correct"] += s["correct"]
    weak = sorted([i for i in st if st[i]["lapses"] > 0 and st[i]["level"] < 3],
                  key=lambda i: (-st[i]["lapses"], st[i]["level"]))[:7]
    total, mastered = len(st), sum(1 for s in st.values() if s["level"] == 3)
    seen_sum = sum(s["seen"] for s in st.values())
    acc = sum(s["correct"] for s in st.values()) / seen_sum if seen_sum else None

    # 남은 단계와 시간 계산 (단계 = 숙련까지 남은 정답 수)
    def steps(ids):
        return sum(max(0, P["promote"][l]) for i in ids for l in range(st[i]["level"], 3))
    core_ids = [i for i in st if concepts[i].get("weight", 1) >= CORE_WEIGHT]
    rate = pace(lpath)
    h = hours_left(S)
    eff = max(acc or 0.7, 0.4)
    plan = None
    if h is not None and h > 0:
        need_all = steps(st) / eff
        need_core = steps(core_ids) / eff if core_ids else None
        plan = {"hours_left": round(h, 1), "rate_per_hour": round(rate, 1) if rate else None,
                "needed_all": round(need_all), "needed_core": round(need_core) if need_core is not None else None}
        if rate:
            cap = rate * h
            plan["coverage_all_pct"] = min(100, round(cap * 100 / need_all)) if need_all else 100
            if need_core:
                plan["coverage_core_pct"] = min(100, round(cap * 100 / need_core))

    data = {
        "mode": S["mode"], "mode_label": P["label"], "exam_at": S["exam_at"],
        "questions_answered": prog["turn"], "concepts": total, "mastered": mastered,
        "accuracy": round(acc, 2) if acc is not None else None,
        "by_level": {LEVEL_KO[k]: sum(1 for s in st.values() if s["level"] == k) for k in range(4)},
        "by_lecture": lectures, "plan": plan,
        "weakest": [{"id": i, "title": concepts[i]["title"], "lapses": st[i]["lapses"],
                     "level": LEVEL_KO[st[i]["level"]]} for i in weak],
    }
    if args.json:
        return out(data)

    print(f"[{P['label']} 모드] 푼 문제 {prog['turn']}개 · 개념 {total}개 중 숙련 {mastered}개 "
          f"({mastered * 100 // max(total, 1)}%)" + (f" · 정답률 {acc:.0%}" if acc is not None else ""))
    print("단계별: " + " / ".join(f"{k} {v}" for k, v in data["by_level"].items()))
    if plan:
        print(f"\n시험까지 {plan['hours_left']}시간 (잠·밥·이동 시간도 여기서 빠진다는 걸 감안할 것)")
        print(f"  숙련까지 필요한 문제: 전체 약 {plan['needed_all']}개"
              + (f", 핵심만 약 {plan['needed_core']}개" if plan["needed_core"] is not None else ""))
        if plan["rate_per_hour"]:
            line = f"  현재 속도 시간당 {plan['rate_per_hour']}문제 → 전체의 {plan['coverage_all_pct']}% 소화 가능"
            if "coverage_core_pct" in plan:
                line += f", 핵심은 {plan['coverage_core_pct']}%"
            print(line)
            if plan["coverage_all_pct"] < 80 and plan.get("coverage_core_pct", 0) > plan["coverage_all_pct"]:
                print("  → 전체를 다 보기 어렵습니다. 핵심만(--core)으로 좁히는 걸 추천.")
        else:
            print("  현재 속도: 측정 중 (최근 2시간 기록이 5문제 이상 쌓이면 계산)")
    print("\n강의별 (객관식/단답/서술/숙련, 미출제, 정답률)")
    for lec in sorted(lectures, key=lec_key):
        d = lectures[lec]
        a = f"{d['correct'] * 100 // d['seen']}%" if d["seen"] else "-"
        print(f"  {lec}강: {'/'.join(map(str, d['levels']))}  미출제 {d['unseen']}  정답률 {a}")
    if data["weakest"]:
        print("\n약점 개념")
        for w in data["weakest"]:
            print(f"  {w['id']} {w['title']} — 틀린 횟수 {w['lapses']}, 현재 {w['level']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".", help="과목 폴더 (기본: 현재 폴더)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    m = sub.add_parser("mode")
    m.add_argument("name", nargs="?", choices=list(MODES))
    m.add_argument("--until", help='시험 시각. 예: "2026-10-22 09:00"')
    m.add_argument("--clear-until", action="store_true")
    p = sub.add_parser("pick")
    p.add_argument("--batch", type=int, help="객관식·단답식 묶음 크기 (예: 5)")
    p.add_argument("--lecture", help="예: 3 또는 3,5")
    p.add_argument("--core", action="store_true", help="핵심 개념(weight 2)만")
    p.add_argument("--weak", action="store_true")
    p.add_argument("--final", action="store_true", help="마지막 점검 (숙련 개념 단답식 한 바퀴)")
    r = sub.add_parser("record")
    r.add_argument("ids")
    r.add_argument("result", choices=["correct", "unsure", "partial", "wrong", "guess", "hint"])
    r.add_argument("--asked", required=True, choices=["mc", "short", "essay", "mastered", "combo"],
                   help="pick이 준 level 그대로")
    r.add_argument("--note", help="이번 문제 한 줄 요약 (같은 문제 반복 방지)")
    r.add_argument("--score", type=int)
    sub.add_parser("undo")
    sub.add_parser("final")
    sm = sub.add_parser("summary")
    sm.add_argument("--lecture")
    sm.add_argument("--core", action="store_true")
    s = sub.add_parser("stats")
    s.add_argument("--json", action="store_true")
    args = ap.parse_args()
    {"init": cmd_init, "mode": cmd_mode, "pick": cmd_pick, "record": cmd_record, "undo": cmd_undo,
     "final": cmd_final, "summary": cmd_summary, "stats": cmd_stats}[args.cmd](args)


if __name__ == "__main__":
    main()
