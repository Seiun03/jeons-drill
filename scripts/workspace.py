#!/usr/bin/env python3
"""공부용 기본 폴더와 과목 폴더를 관리한다.

    python <SKILL_DIR>/scripts/workspace.py status            현재 상황(JSON): 기본 폴더, 지금 위치, 과목 목록
    python <SKILL_DIR>/scripts/workspace.py set-base [경로]     기본 폴더 지정 (생략하면 문서\\jeons-drill)
    python <SKILL_DIR>/scripts/workspace.py new <과목> [--open] 과목 폴더와 materials\\ 생성
    python <SKILL_DIR>/scripts/workspace.py find <과목>         과목 이름으로 폴더 찾기
    python <SKILL_DIR>/scripts/workspace.py open <경로>         파일 탐색기로 열기

설정 파일: ~/.jeons-drill/config.json  (스킬 폴더 밖이라 업데이트해도 유지됨)
과목 폴더 = materials/ 또는 .drill/ 이 있는 폴더.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CONFIG = Path.home() / ".jeons-drill" / "config.json"
BAD_CHARS = set('<>:"/\\|?*')


def documents_dir() -> Path:
    if os.name == "nt":
        try:  # OneDrive로 옮겨진 문서 폴더까지 정확히 찾는다
            import ctypes
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]

            fid = GUID(0xFDD39AD0, 0x238F, 0x46AF, (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
            buf = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(fid), 0, None, ctypes.byref(buf)) == 0:
                p = Path(buf.value)
                ctypes.windll.ole32.CoTaskMemFree(buf)
                return p
        except Exception:
            pass
    else:
        try:
            out = subprocess.run(["xdg-user-dir", "DOCUMENTS"], capture_output=True, text=True, timeout=5).stdout.strip()
            if out and Path(out) != Path.home():
                return Path(out)
        except Exception:
            pass
    return Path.home() / "Documents"


DEFAULT_BASE = documents_dir() / "jeons-drill"


def load_config():
    if CONFIG.exists():
        try:
            return json.loads(CONFIG.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_config(cfg):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def base_dir():
    b = load_config().get("base")
    return Path(b) if b else None


def is_subject(p: Path) -> bool:
    return (p / "materials").is_dir() or (p / ".drill").is_dir()


def subject_info(p: Path):
    mats = [f for f in (p / "materials").glob("*") if f.is_file() and f.suffix.lower() in (".pdf", ".ppt", ".pptx")] \
        if (p / "materials").is_dir() else []
    info = {"name": p.name, "path": str(p), "materials": len(mats),
            "setup_done": (p / ".drill" / "concepts.json").exists(), "mode": None, "answered": 0}
    prog = p / ".drill" / "progress.json"
    if prog.exists():
        try:
            d = json.loads(prog.read_text(encoding="utf-8"))
            info["mode"] = d.get("settings", {}).get("mode", "normal")
            info["answered"] = d.get("turn", 0)
        except Exception:
            pass
    return info


def list_subjects(base: Path):
    if not base or not base.is_dir():
        return []
    return [subject_info(c) for c in sorted(base.iterdir()) if c.is_dir() and not c.name.startswith(".") and is_subject(c)]


def same(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except Exception:
        return False


def under(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def out(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_status(args):
    cwd = Path(args.cwd or os.getcwd())
    base = base_dir()
    res = {"config": str(CONFIG), "base": str(base) if base else None, "default_base": str(DEFAULT_BASE),
           "cwd": str(cwd), "cwd_kind": "other", "current_subject": None}
    if is_subject(cwd):
        res["cwd_kind"], res["current_subject"] = "subject", subject_info(cwd)
    elif base and same(cwd, base):
        res["cwd_kind"] = "base"
    elif base and under(cwd, base):
        rel = cwd.resolve().relative_to(base.resolve())
        top = base / rel.parts[0]
        res["cwd_kind"] = "inside_base"
        if is_subject(top):
            res["current_subject"] = subject_info(top)
    if base:
        res["base_is_open_folder"] = res["cwd_kind"] in ("base", "inside_base") or (
            res["cwd_kind"] == "subject" and under(cwd, base))
    res["subjects"] = list_subjects(base) if base else []
    out(res)


def cmd_set_base(args):
    p = Path(os.path.expandvars(os.path.expanduser(args.path))) if args.path else DEFAULT_BASE
    p.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    cfg["base"] = str(p.resolve())
    save_config(cfg)
    out({"base": cfg["base"], "config": str(CONFIG)})


def open_path(p: Path):
    try:
        if os.name == "nt":
            os.startfile(str(p))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def cmd_new(args):
    base = base_dir()
    if not base:
        out({"error": "기본 폴더가 아직 없습니다. 먼저 set-base를 실행하세요."})
        sys.exit(1)
    name = args.name.strip().strip(".")
    if not name or any(ch in BAD_CHARS for ch in name):
        out({"error": f"폴더 이름으로 쓸 수 없는 문자가 있습니다: {args.name}"})
        sys.exit(1)
    subj = base / name
    existed = subj.exists()
    (subj / "materials").mkdir(parents=True, exist_ok=True)
    opened = open_path(subj / "materials") if args.open else False
    out({"subject": name, "path": str(subj), "materials": str(subj / "materials"),
         "already_existed": existed, "opened": opened})


def cmd_find(args):
    base = base_dir()
    subs = list_subjects(base)
    q = args.name.strip().lower()
    exact = [s for s in subs if s["name"].lower() == q]
    part = exact or [s for s in subs if q in s["name"].lower()]
    out({"query": args.name, "matches": part, "all": [s["name"] for s in subs]})


def cmd_open(args):
    p = Path(args.path)
    out({"path": str(p), "opened": p.exists() and open_path(p)})


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status")
    s.add_argument("--cwd")
    b = sub.add_parser("set-base")
    b.add_argument("path", nargs="?")
    n = sub.add_parser("new")
    n.add_argument("name")
    n.add_argument("--open", action="store_true")
    f = sub.add_parser("find")
    f.add_argument("name")
    o = sub.add_parser("open")
    o.add_argument("path")
    args = ap.parse_args()
    {"status": cmd_status, "set-base": cmd_set_base, "new": cmd_new, "find": cmd_find, "open": cmd_open}[args.cmd](args)


if __name__ == "__main__":
    main()
