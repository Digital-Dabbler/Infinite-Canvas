#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
cleanup_audit.py - 纯只读清理审计（dry-run）

不删除、不移动、不修改任何文件。只输出"如果开启自动清理，本轮会动哪些文件"。

判定标准是"是否仍被引用"，不是"存了多久"；时间仅作辅助缓冲条件。

用法:
    python tools\cleanup_audit.py                 # 摘要
    python tools\cleanup_audit.py --verbose       # 附可疑项明细
    python tools\cleanup_audit.py --json out.json # 导出机器可读清单
    python tools\cleanup_audit.py --orphan-age 14 --preview-age 30
"""

import argparse
import glob
import json
import os
import re
import sys
import time
import urllib.parse

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")

# --- 与 main.py 对齐的路径 ---
CANVAS_DIR = os.path.join(DATA_DIR, "canvases")
CONVERSATION_DIR = os.path.join(DATA_DIR, "conversations")
MEDIA_PREVIEW_DIR = os.path.join(DATA_DIR, "media_previews")
WORKFLOW_DIR = os.path.join(BASE_DIR, "workflows")
HISTORY_FILE = os.path.join(BASE_DIR, "history.json")
STORAGE_SETTINGS_FILE = os.path.join(DATA_DIR, "storage_settings.json")

# 引用源清单。缺一个就可能误删，所以显式列全。
REFERENCE_FILES = [
    os.path.join(DATA_DIR, "asset_library.json"),
    os.path.join(DATA_DIR, "asset_trash.json"),
    os.path.join(DATA_DIR, "asset_url_library.json"),
    os.path.join(DATA_DIR, "canvas_asset_index_cache.json"),
    os.path.join(DATA_DIR, "prompt_libraries.json"),
    os.path.join(DATA_DIR, "upstream_task_scopes.json"),
    os.path.join(DATA_DIR, "photoshop_bridge_tasks.json"),
    os.path.join(DATA_DIR, "projects.json"),
    HISTORY_FILE,
]
# canvas_tasks.json 刻意不在上面：它同时含已终结(failed/cancelled)任务，
# 全文扫描会让失败产出被永久保护。改由 active_task_tokens() 只贡献
# queued/running 任务的 token。
REFERENCE_GLOBS = [
    os.path.join(CANVAS_DIR, "**", "*.json"),        # 含 deleted_at 回收站画布
    os.path.join(CONVERSATION_DIR, "**", "*"),
    os.path.join(DATA_DIR, "usage_audit", "**", "*.jsonl"),
    os.path.join(WORKFLOW_DIR, "**", "*.json"),
]

MS_DAY = 24 * 60 * 60
TOKEN_RE = re.compile(r"[A-Za-z0-9]{8,}")
ACTIVE_TASK_STATUS = {"queued", "running"}   # 对齐 main.py:5158


class AuditAborted(Exception):
    """引用源不可读 -> 整轮放弃。绝不把'读不到'当成'无引用'。"""


def _storage_abs_path(value, fallback):
    text = str(value or "").strip()
    if not text:
        return os.path.abspath(fallback)
    text = os.path.expanduser(os.path.expandvars(text))
    if not os.path.isabs(text):
        text = os.path.join(BASE_DIR, text)
    return os.path.abspath(text)


def resolve_storage_dirs():
    """复用 main.py 的 storage_settings 语义，绝不硬编码 assets/output。"""
    defaults = {
        "upload": os.path.join(BASE_DIR, "assets", "input"),
        "generated": os.path.join(BASE_DIR, "assets", "output"),
        "local": os.path.join(BASE_DIR, "assets", "uploads"),
    }
    raw = {}
    if os.path.exists(STORAGE_SETTINGS_FILE):
        try:
            with open(STORAGE_SETTINGS_FILE, "r", encoding="utf-8-sig") as fh:
                raw = json.load(fh) or {}
        except Exception as exc:
            raise AuditAborted("storage_settings.json 不可解析: %s" % exc)
    return {k: _storage_abs_path(raw.get(k), v) for k, v in defaults.items()}


def read_text_strict(path):
    """读取失败必须抛出，由调用方决定中止。"""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read()
    except OSError as exc:
        raise AuditAborted("引用源不可读 %s: %s" % (os.path.relpath(path, BASE_DIR), exc))


def collect_reference_sources():
    paths = []
    for p in REFERENCE_FILES:
        if os.path.isfile(p):
            paths.append(p)
    for pattern in REFERENCE_GLOBS:
        for p in glob.glob(pattern, recursive=True):
            if os.path.isfile(p):
                paths.append(p)
    return sorted(set(paths))


def build_reference_index(paths):
    """返回 (token 集合, 原始语料长度)。token = 文件名中 >=8 位字母数字串。"""
    tokens = set()
    total = 0
    for path in paths:
        raw = read_text_strict(path)
        total += len(raw)
        for text in (raw, urllib.parse.unquote(raw)):
            for m in TOKEN_RE.finditer(text):
                tokens.add(m.group(0).lower())
    return tokens, total


def active_task_tokens():
    """进行中任务的产出可能尚未写入画布，其 token 一律视为已引用。"""
    path = os.path.join(DATA_DIR, "canvas_tasks.json")
    if not os.path.isfile(path):
        return set(), 0
    raw = read_text_strict(path)
    try:
        data = json.loads(raw)
    except Exception as exc:
        raise AuditAborted("canvas_tasks.json 不可解析: %s" % exc)
    tasks = data.get("tasks") if isinstance(data, dict) else data
    if isinstance(tasks, dict):
        tasks = list(tasks.values())
    if not isinstance(tasks, list):
        tasks = []
    tokens = set()
    active = 0
    for task in tasks:
        if not isinstance(task, dict):
            continue
        if str(task.get("status") or "").lower() not in ACTIVE_TASK_STATUS:
            continue
        active += 1
        blob = json.dumps(task, ensure_ascii=False)
        for text in (blob, urllib.parse.unquote(blob)):
            for m in TOKEN_RE.finditer(text):
                tokens.add(m.group(0).lower())
    return tokens, active


def scan_media(dirs, ref_tokens, orphan_age_days, now):
    """扫描 upload/generated 两类目录，分类为 referenced / fresh / orphan。"""
    cutoff = now - orphan_age_days * MS_DAY
    buckets = {}
    for key in ("upload", "generated"):
        root = dirs[key]
        allowed_prefix = os.path.realpath(root) + os.sep
        stat = {
            "root": root, "total": 0, "total_bytes": 0,
            "referenced": 0, "referenced_bytes": 0,
            "fresh": 0, "fresh_bytes": 0,
            "no_token": 0, "no_token_bytes": 0,
            "orphan": 0, "orphan_bytes": 0, "orphans": [],
        }
        if os.path.isdir(root):
            for dirpath, dirnames, files in os.walk(root):
                dirnames[:] = [d for d in dirnames if d != ".trash"]
                for fn in files:
                    fp = os.path.join(dirpath, fn)
                    try:
                        st = os.stat(fp)
                    except OSError:
                        continue
                    stat["total"] += 1
                    stat["total_bytes"] += st.st_size
                    stem = os.path.splitext(fn)[0]
                    toks = [t.lower() for t in TOKEN_RE.findall(stem)]
                    if not toks:
                        # 无可辨识 token -> 无法安全判定，保守保留
                        stat["no_token"] += 1
                        stat["no_token_bytes"] += st.st_size
                        continue
                    if any(t in ref_tokens for t in toks):
                        stat["referenced"] += 1
                        stat["referenced_bytes"] += st.st_size
                        continue
                    if st.st_mtime > cutoff:
                        stat["fresh"] += 1
                        stat["fresh_bytes"] += st.st_size
                        continue
                    # 路径安全：必须落在目标根内
                    if not os.path.realpath(fp).startswith(allowed_prefix):
                        continue
                    stat["orphan"] += 1
                    stat["orphan_bytes"] += st.st_size
                    stat["orphans"].append({
                        "path": fp,
                        "rel": os.path.relpath(fp, BASE_DIR),
                        "bytes": st.st_size,
                        "age_days": round((now - st.st_mtime) / MS_DAY, 1),
                    })
        stat["orphans"].sort(key=lambda x: -x["bytes"])
        buckets[key] = stat
    return buckets


def scan_previews(preview_age_days, now):
    cutoff = now - preview_age_days * MS_DAY
    stat = {"root": MEDIA_PREVIEW_DIR, "total": 0, "total_bytes": 0,
            "stale": 0, "stale_bytes": 0}
    if os.path.isdir(MEDIA_PREVIEW_DIR):
        for dirpath, _, files in os.walk(MEDIA_PREVIEW_DIR):
            for fn in files:
                fp = os.path.join(dirpath, fn)
                try:
                    st = os.stat(fp)
                except OSError:
                    continue
                stat["total"] += 1
                stat["total_bytes"] += st.st_size
                if st.st_mtime <= cutoff:
                    stat["stale"] += 1
                    stat["stale_bytes"] += st.st_size
    return stat


def gb(n):
    return n / 1024.0 ** 3


def mb(n):
    return n / 1024.0 ** 2


def apply_batch_caps(buckets, max_files, max_gb):
    """每轮上限，限制误判爆炸半径。按体积降序取，超限的标为 deferred。"""
    limit_bytes = max_gb * 1024 ** 3
    picked, files, total = [], 0, 0
    merged = []
    for key in ("generated", "upload"):
        for o in buckets[key]["orphans"]:
            merged.append((key, o))
    merged.sort(key=lambda x: -x[1]["bytes"])
    for key, o in merged:
        if files >= max_files or total + o["bytes"] > limit_bytes:
            continue
        picked.append((key, o))
        files += 1
        total += o["bytes"]
    return picked, files, total, len(merged) - files


def main():
    ap = argparse.ArgumentParser(description="清理审计 dry-run（只读）")
    ap.add_argument("--orphan-age", type=int, default=14, help="孤儿最小年龄（天），默认 14")
    ap.add_argument("--preview-age", type=int, default=30, help="预览缓存最大年龄（天），默认 30")
    ap.add_argument("--max-files", type=int, default=2000, help="单轮最多文件数，默认 2000")
    ap.add_argument("--max-gb", type=float, default=5.0, help="单轮最多 GB，默认 5.0")
    ap.add_argument("--verbose", action="store_true", help="打印明细")
    ap.add_argument("--json", metavar="PATH", help="导出 JSON 清单")
    args = ap.parse_args()

    now = time.time()
    print("=" * 68)
    print("清理审计 dry-run（只读，不会删除或移动任何文件）")
    print("=" * 68)

    try:
        dirs = resolve_storage_dirs()
        print("\n[存储路径]（来自 data/storage_settings.json，未硬编码）")
        for k, v in dirs.items():
            print("  %-10s %s" % (k, v))

        sources = collect_reference_sources()
        ref_tokens, corpus_len = build_reference_index(sources)
        task_tokens, active_tasks = active_task_tokens()
        ref_tokens |= task_tokens

        canvas_total = len(glob.glob(os.path.join(CANVAS_DIR, "**", "*.json"), recursive=True))
        print("\n[引用源]  全部读取成功，任何一个失败都会整轮中止")
        print("  引用源文件      %d 个（画布 %d 个，含回收站中 deleted_at 画布）" % (len(sources), canvas_total))
        print("  语料            %.1f MB" % mb(corpus_len))
        print("  引用 token      %d 个" % len(ref_tokens))
        print("  进行中任务      %d 个（queued/running，其产出已强制视为已引用）" % active_tasks)

        buckets = scan_media(dirs, ref_tokens, args.orphan_age, now)
        preview = scan_previews(args.preview_age, now)
    except AuditAborted as exc:
        print("\n[中止] %s" % exc)
        print("按设计要求：引用源不完整时整轮放弃，不做任何清理。")
        return 2

    print("\n" + "-" * 68)
    print("第0层 · 预览缓存（data/media_previews，可重建，无需引用检查）")
    print("-" * 68)
    print("  现有            %d 个文件 %.2f GB" % (preview["total"], gb(preview["total_bytes"])))
    print("  超过 %d 天       %d 个文件 %.2f GB  <- 本轮会删除" % (
        args.preview_age, preview["stale"], gb(preview["stale_bytes"])))

    print("\n" + "-" * 68)
    print("第1层 · 孤儿回收（无引用 且 年龄 > %d 天 -> 移入 .trash，30 天后真删）" % args.orphan_age)
    print("-" * 68)
    print("  %-10s %8s %10s %8s %10s %7s %9s" % ("目录", "总数", "总量GB", "有引用", "有引用GB", "孤儿", "孤儿GB"))
    for key, label in (("generated", "generated"), ("upload", "upload")):
        s = buckets[key]
        print("  %-10s %8d %10.2f %8d %10.2f %7d %9.2f" % (
            label, s["total"], gb(s["total_bytes"]),
            s["referenced"], gb(s["referenced_bytes"]),
            s["orphan"], gb(s["orphan_bytes"])))
    print("\n  保护性保留（不会动）：")
    for key in ("generated", "upload"):
        s = buckets[key]
        print("    %-10s 未满 %d 天 %d 个 %.2f GB | 无可辨识 token %d 个 %.2f GB" % (
            key, args.orphan_age, s["fresh"], gb(s["fresh_bytes"]),
            s["no_token"], gb(s["no_token_bytes"])))

    picked, pf, pb, deferred = apply_batch_caps(buckets, args.max_files, args.max_gb)
    print("\n" + "-" * 68)
    print("本轮实际动作（受单轮上限 %d 文件 / %.1f GB 约束）" % (args.max_files, args.max_gb))
    print("-" * 68)
    print("  删除预览缓存    %d 个文件 %.2f GB" % (preview["stale"], gb(preview["stale_bytes"])))
    print("  移入回收站      %d 个文件 %.2f GB" % (pf, gb(pb)))
    print("  延后到下轮      %d 个文件" % deferred)
    print("  ---------------------------------------------")
    print("  本轮释放合计    %.2f GB" % gb(preview["stale_bytes"] + pb))

    if args.verbose and picked:
        print("\n[明细] 将移入回收站的最大 30 项：")
        for key, o in picked[:30]:
            print("  %8.1f MB  %5.0f天  %s" % (mb(o["bytes"]), o["age_days"], o["rel"]))

    suspicious = [(k, o) for k, o in picked if o["bytes"] > 20 * 1024 ** 2]
    if suspicious:
        print("\n[可疑项] 单文件 >20MB 的孤儿 %d 个，建议人工确认：" % len(suspicious))
        for key, o in suspicious[:15]:
            print("  %8.1f MB  %5.0f天  %s" % (mb(o["bytes"]), o["age_days"], o["rel"]))

    if args.json:
        payload = {
            "generated_at": int(now),
            "dry_run": True,
            "params": {
                "orphan_age_days": args.orphan_age,
                "preview_age_days": args.preview_age,
                "max_files": args.max_files,
                "max_gb": args.max_gb,
            },
            "storage_dirs": dirs,
            "reference_sources": len(sources),
            "reference_tokens": len(ref_tokens),
            "active_tasks": active_tasks,
            "preview": {k: v for k, v in preview.items() if k != "root"},
            "summary": {
                key: {k: v for k, v in buckets[key].items() if k != "orphans"}
                for key in buckets
            },
            "planned_trash": [
                {"bucket": k, "rel": o["rel"], "bytes": o["bytes"], "age_days": o["age_days"]}
                for k, o in picked
            ],
        }
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print("\nJSON 清单已导出: %s" % args.json)

    print("\n提示：本脚本为只读审计。确认结果无误后，才应在 main.py 中启用自动清理。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
