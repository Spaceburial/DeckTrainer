#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeckTrainer v2 —— SteamOS / Steam Deck 通用游戏修改器（零依赖单文件）

通用性：
  · 支持所有 Steam/Proton/Wine 游戏（任何 .exe 进程）
  · 支持非 Steam 游戏、原生 Linux 游戏、模拟器（所有用户进程都可附加）
  · 全类型搜索：一次同时尝试 4/8 字节整数、单/双精度小数
  · 地址收藏：保存常用数值，下次开游戏一键按旧值重搜

用法：
  1) 游戏模式：Steam 启动选项  bash "/path/start.sh" %command%
     修改器作为游戏父进程自动启动，手机浏览器 http://Deck的IP:8766 控制
  2) 桌面模式：双击 DeckTrainer.desktop，输入 sudo 密码后浏览器打开 127.0.0.1:8766
  3) 自检：python3 trainer.py --selftest <pid> <数值>

原理：直接读写 /proc/<pid>/mem，不暂停游戏进程（不会把游戏冻住）。
仅限单机游戏，请勿用于联机对战。
"""

import argparse
import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time
from array import array
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

PORT_DEFAULT = 8766
CHUNK = 8 * 1024 * 1024
OVERLAP = 8
MAX_MATCHES = 2_000_000
FUZZY_CAP = 32 * 1024 * 1024
FUZZY_MAX_REGION = 24 * 1024 * 1024
CAND_CAP = 200_000

TYPE_SPECS = {
    "int32": ("i", 4), "uint32": ("I", 4),
    "int64": ("q", 8), "uint64": ("Q", 8),
    "float": ("f", 4), "double": ("d", 8),
}
TYPE_LABEL = {
    "int32": "4字节整数", "uint32": "4字节无符号",
    "int64": "8字节整数", "uint64": "8字节无符号",
    "float": "单精度小数", "double": "双精度小数",
}

GAMES = [
    {"key": "pbz", "exe": "PBZ.exe", "name": "影之刃零",
     "tip": "残尘/意识尘埃用 4字节整数；剩余天数用 4字节整数；生命/杀气无数字显示时用「模糊快照→受伤后选减少」。",
     "values": [
         {"label": "残尘 / 意识尘埃", "type": "int32", "how": "看背包里的残尘数字 → 精确搜索 → 花掉/获得后『精确值』再搜"},
         {"label": "剩余天数", "type": "int32", "how": "看存档界面的天数 → 精确搜索 → 过一天后『减少』筛选"},
         {"label": "生命值", "type": "float", "how": "模糊快照 → 被打一下 → 『减少』→ 再被打 → 『减少』，剩几条即血量"},
         {"label": "杀气", "type": "float", "how": "模糊快照 → 用一次杀气技能 → 『减少』→ 回满后『增加』定位"},
     ]},
    {"key": "rottr", "exe": "RiseOfTheTombRaider.exe", "name": "古墓丽影：崛起",
     "tip": "生命/耐力是 4字节小数；金币、技能点、弹药是 4字节整数。",
     "values": [
         {"label": "生命值", "type": "float", "how": "模糊快照 → 被敌人打中掉血 → 『减少』→ 再被打 → 『减少』"},
         {"label": "金币 / 资源", "type": "int32", "how": "看数值 → 精确搜索 → 消耗或获得后『精确值』再搜"},
         {"label": "技能点", "type": "int32", "how": "看技能点数字 → 精确搜索 → 花掉一点后『减少』"},
         {"label": "弹药", "type": "int32", "how": "开枪后看当前弹匣数 → 精确搜索 → 再开枪『减少』"},
     ]},
    {"key": "outerwilds", "exe": "OuterWilds.exe", "name": "星际拓荒",
     "tip": "氧气/燃料/生命是小数；本作有循环重置机制，锁定值在重置后要重新搜。",
     "values": [
         {"label": "氧气", "type": "float", "how": "模糊快照 → 憋气消耗 → 『减少』→ 再等几秒 → 『减少』"},
         {"label": "生命值", "type": "float", "how": "模糊快照 → 受伤 → 『减少』→ 再受伤 → 『减少』"},
         {"label": "燃料", "type": "float", "how": "推进消耗燃料后『减少』→ 补充后『增加』定位"},
     ]},
    {"key": "cp2077", "exe": "Cyberpunk2077.exe", "name": "赛博朋克2077",
     "tip": "欧元是 4字节整数；属性点/技能点是 4字节整数；生命是 4字节小数。",
     "values": [
         {"label": "欧元 (Eddies)", "type": "int32", "how": "看钱包金额 → 精确搜索 → 买卖一次后『精确值』再搜"},
         {"label": "属性点", "type": "int32", "how": "看属性点数字 → 精确搜索 → 加一点后『减少』"},
         {"label": "生命值", "type": "float", "how": "模糊快照 → 被打 → 『减少』→ 再被打 → 『减少』"},
     ]},
]


# ---------------------------------------------------------------- 进程 / 内存

def list_processes():
    """列出所有可选进程：Steam/Proton 游戏、Wine 程序、原生 Linux 程序。"""
    procs = []
    for pid_str in os.listdir("/proc"):
        if not pid_str.isdigit():
            continue
        pid = int(pid_str)
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmd = [c for c in f.read().split(b"\0") if c]
            if not cmd:
                continue
            exe_path = cmd[0].decode("utf-8", "replace")
            name = os.path.basename(exe_path.rstrip("/")) or f"pid {pid}"
            try:
                real = os.readlink(f"/proc/{pid}/exe")
                if real and not real.startswith("/"):
                    real = exe_path
                name = os.path.basename(real) or name
            except OSError:
                pass
            rss = 0
            with open(f"/proc/{pid}/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        rss = int(line.split()[1]) * 1024
                        break
            if rss < 8 * 1024 * 1024:
                continue
            if "/steamapps/" in exe_path:
                kind = "steam"
            elif name.lower().endswith(".exe"):
                kind = "wine"
            else:
                kind = "native"
            game = next((g for g in GAMES if g["exe"].lower() == name.lower()), None)
            procs.append({
                "pid": pid, "exe": name,
                "name": game["name"] if game else name,
                "key": game["key"] if game else "",
                "kind": kind, "rss": rss,
            })
        except (OSError, ValueError):
            continue
    order = {"steam": 0, "wine": 1, "native": 2}
    procs.sort(key=lambda p: (-int(bool(p["key"])), order[p["kind"]], -p["rss"]))
    return procs


def list_regions(pid, include_modules=False):
    regions = []
    try:
        with open(f"/proc/{pid}/maps") as f:
            for line in f:
                parts = line.rstrip("\n").split(maxsplit=5)
                if len(parts) < 2:
                    continue
                addr, perms = parts[0], parts[1]
                path = parts[5].strip() if len(parts) > 5 else ""
                if not perms.startswith("rw") or perms[3] != "p":
                    continue
                if path.startswith("[v") or "/dev/" in path or "memfd:" in path:
                    continue
                is_anon = (not path) or path in ("[heap]", "[stack]") or path.startswith("[anon")
                if not include_modules and not is_anon:
                    if not any(k in path for k in ("steamapps", "compatdata", "drive_c")):
                        continue
                try:
                    start, end = addr.split("-")
                    base, size = int(start, 16), int(end, 16) - int(start, 16)
                except ValueError:
                    continue
                if size < 4096:
                    continue
                regions.append((base, size))
    except OSError:
        pass
    regions.sort()
    merged = []
    for base, size in regions:
        if merged and merged[-1][0] + merged[-1][1] == base:
            merged[-1] = (merged[-1][0], merged[-1][1] + size)
        else:
            merged.append((base, size))
    return merged


def open_mem(pid, write=False):
    return os.open(f"/proc/{pid}/mem", os.O_RDWR if write else os.O_RDONLY)


def _read_value_fd(fd, addr, vtype):
    fmt, size = TYPE_SPECS[vtype]
    try:
        data = os.pread(fd, size, addr)
    except OSError:
        return None
    if len(data) < size:
        return None
    return struct.unpack("<" + fmt, data)[0]


def _write_value_fd(fd, addr, vtype, value):
    fmt, size = TYPE_SPECS[vtype]
    try:
        os.pwrite(fd, struct.pack("<" + fmt, value), addr)
        return True
    except (OSError, struct.error):
        return False


def _typecode(vtype):
    return {"int32": "i", "uint32": "I", "int64": "q",
            "uint64": "Q", "float": "f", "double": "d"}[vtype]


def coerce_value(vtype, raw):
    if raw is None or raw == "":
        return None
    if vtype in ("float", "double"):
        return float(raw)
    return int(float(raw))


def iter_regions_chunks(pid, fd, regions):
    for base, size in regions:
        off = 0
        while off < size:
            n = min(CHUNK, size - off)
            try:
                data = os.pread(fd, n + (OVERLAP if off + n < size else 0), base + off)
            except OSError:
                data = b""
            yield base + off, off, n, data
            off += n


def scan_exact(pid, regions, vtype, value):
    fmt, size = TYPE_SPECS[vtype]
    pat = struct.pack("<" + fmt, value)
    out = []
    fd = open_mem(pid)
    try:
        for addr_base, off, n, data in iter_regions_chunks(pid, fd, regions):
            if len(out) >= MAX_MATCHES:
                break
            if not data:
                continue
            limit = min(n, len(data))
            pos = 0
            while len(out) < MAX_MATCHES:
                idx = data.find(pat, pos)
                if idx < 0 or idx >= limit:
                    break
                addr = addr_base + idx
                if addr % size == 0:
                    out.append(addr)
                pos = idx + 1
    finally:
        os.close(fd)
    return out


def build_patterns(raw):
    """根据输入生成 6 种数值类型的搜索模式（自动去重）。"""
    pats = []
    seen = set()
    f = float(raw)
    is_whole = raw.strip().lstrip("+-").isdigit()
    cands = []
    if is_whole:
        i = int(f)
        cands += [("int32", "i", i), ("uint32", "I", i), ("int64", "q", i), ("uint64", "Q", i)]
    cands += [("float", "f", f), ("double", "d", f)]
    for t, fmt, v in cands:
        try:
            p = struct.pack("<" + fmt, v)
        except struct.error:
            continue
        if p in seen:
            continue
        seen.add(p)
        pats.append((t, p, TYPE_SPECS[t][1]))
    return pats


def scan_multi(pid, regions, raw):
    """全类型搜索：一次读内存，同时匹配所有类型。"""
    pats = build_patterns(raw)
    out = []
    fd = open_mem(pid)
    try:
        for addr_base, off, n, data in iter_regions_chunks(pid, fd, regions):
            if len(out) >= MAX_MATCHES:
                break
            if not data:
                continue
            limit = min(n, len(data))
            for t, pat, tsize in pats:
                pos = 0
                while len(out) < MAX_MATCHES:
                    idx = data.find(pat, pos)
                    if idx < 0 or idx >= limit:
                        break
                    addr = addr_base + idx
                    if addr % tsize == 0:
                        out.append({"addr": addr, "type": t,
                                    "value": coerce_value(t, raw)})
                    pos = idx + 1
    finally:
        os.close(fd)
    return out


def fuzzy_snapshot(pid, regions, vtype, cap_bytes=FUZZY_CAP):
    tc = _typecode(vtype)
    size = TYPE_SPECS[vtype][1]
    snaps = []
    total = 0
    fd = open_mem(pid)
    try:
        for base, size_ in regions:
            if size_ > FUZZY_MAX_REGION or base % size != 0:
                continue
            if total + size_ > cap_bytes:
                break
            n = size_ // size
            try:
                raw = os.pread(fd, n * size, base)
            except OSError:
                continue
            if len(raw) < n * size:
                n = len(raw) // size
                if n == 0:
                    continue
                raw = raw[: n * size]
            arr = array(tc)
            arr.frombytes(raw)
            snaps.append({"base": base, "type": vtype, "arr": arr})
            total += n * size
    finally:
        os.close(fd)
    return snaps


def fuzzy_refine(pid, snaps, op, cap=CAND_CAP, tol_rel=0.003):
    size = TYPE_SPECS[snaps[0]["type"]][1]
    vtype = snaps[0]["type"]
    is_float = vtype in ("float", "double")
    out = []
    truncated = False
    fd = open_mem(pid)
    try:
        for snap in snaps:
            n = len(snap["arr"])
            try:
                raw = os.pread(fd, n * size, snap["base"])
            except OSError:
                continue
            if len(raw) < n * size:
                continue
            cur = array(_typecode(vtype))
            cur.frombytes(raw[: n * size])
            prev = snap["arr"]
            base = snap["base"]
            for i in range(n):
                p, c = prev[i], cur[i]
                if is_float:
                    tol = max(abs(p) * tol_rel, 1e-3)
                    hit = {"changed": abs(c - p) > tol, "unchanged": abs(c - p) <= tol,
                           "inc": c - p > tol, "dec": p - c > tol}.get(op, False)
                else:
                    hit = {"changed": c != p, "unchanged": c == p,
                           "inc": c > p, "dec": c < p}.get(op, False)
                if hit:
                    out.append({"addr": base + i * size, "type": vtype, "value": c})
                    if len(out) >= cap:
                        truncated = True
                        break
            if truncated:
                break
    finally:
        os.close(fd)
    return out, truncated


def refine_cells(pid, cells, op, raw_value=None, tol_rel=0.003):
    out = []
    fd = open_mem(pid)
    try:
        for cell in cells:
            vtype = cell["type"]
            cur = _read_value_fd(fd, cell["addr"], vtype)
            if cur is None:
                continue
            prev = cell["value"]
            is_float = vtype in ("float", "double")
            if op == "exact":
                target = coerce_value(vtype, raw_value)
                if target is None:
                    continue
                if is_float:
                    hit = abs(cur - target) <= max(abs(target) * 0.001, 1e-3)
                else:
                    hit = cur == target
            elif is_float:
                tol = max(abs(prev) * tol_rel, 1e-3)
                hit = {"changed": abs(cur - prev) > tol, "unchanged": abs(cur - prev) <= tol,
                       "inc": cur - prev > tol, "dec": prev - cur > tol}.get(op, False)
            else:
                hit = {"changed": cur != prev, "unchanged": cur == prev,
                       "inc": cur > prev, "dec": cur < prev}.get(op, False)
            if hit:
                out.append({"addr": cell["addr"], "type": vtype, "value": cur})
    finally:
        os.close(fd)
    return out


def read_cells(pid, cells, limit=None):
    rows = []
    fd = open_mem(pid)
    try:
        for i, cell in enumerate(cells):
            if limit and i >= limit:
                break
            cur = _read_value_fd(fd, cell["addr"], cell["type"])
            rows.append({"addr": cell["addr"], "type": cell["type"], "value": cur})
    finally:
        os.close(fd)
    return rows


# ---------------------------------------------------------------- 收藏

BOOKMARK_FILE = os.path.expanduser("~/.config/deck-trainer/bookmarks.json")


def load_bookmarks():
    try:
        with open(BOOKMARK_FILE) as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_bookmarks(items):
    try:
        os.makedirs(os.path.dirname(BOOKMARK_FILE), exist_ok=True)
        with open(BOOKMARK_FILE, "w") as f:
            json.dump(items, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def _maps_lines(pid):
    try:
        with open(f"/proc/{pid}/maps") as f:
            return f.readlines()
    except OSError:
        return []


def module_of(pid, addr):
    """若地址属于某个文件映射（.so/.dll/.exe 等），返回 (路径, 模块基址, 偏移)。"""
    lines = _maps_lines(pid)
    path = None
    for line in lines:
        parts = line.rstrip("\n").split(maxsplit=5)
        if len(parts) < 6:
            continue
        try:
            a, b = parts[0].split("-")
        except ValueError:
            continue
        if int(a, 16) <= addr < int(b, 16):
            path = parts[5]
            break
    if not path or not path.startswith("/"):
        return None
    base = None
    for line in lines:
        parts = line.rstrip("\n").split(maxsplit=5)
        if len(parts) < 6 or parts[5] != path:
            continue
        try:
            start = int(parts[0].split("-")[0], 16)
        except ValueError:
            continue
        base = start if base is None else min(base, start)
    if base is None:
        return None
    return path, base, addr - base


def module_base(pid, path):
    for line in _maps_lines(pid):
        parts = line.rstrip("\n").split(maxsplit=5)
        if len(parts) < 6 or parts[5] != path:
            continue
        try:
            start = int(parts[0].split("-")[0], 16)
        except ValueError:
            continue
        return start
    return None


def relocate_bookmarks(pid, game):
    """按收藏记录自动复原地址：模块偏移优先，堆地址按绝对值验证。"""
    items = [b for b in load_bookmarks() if b.get("game") == game]
    out = []
    if not items:
        with STATE_LOCK:
            STATE["recovered"] = []
        return out
    try:
        fd = open_mem(pid)
    except OSError:
        fd = None
    try:
        for b in items:
            vtype = b.get("type", "int32")
            addr = None
            why = ""
            if b.get("mode") == "module" and b.get("module"):
                base = module_base(pid, b["module"])
                if base is None:
                    why = "模块未加载"
                else:
                    addr = base + int(b.get("offset", 0))
            else:
                addr = b.get("addr")
            cur = _read_value_fd(fd, addr, vtype) if (fd is not None and addr) else None
            saved = b.get("value")
            if cur is None:
                status = "fail"
                why = why or "地址不可读"
            else:
                is_float = vtype in ("float", "double")
                same = (abs(cur - float(saved)) <= max(abs(float(saved)) * 0.01, 1e-3)
                        if is_float else cur == saved)
                status = "ok" if same else "mismatch"
            out.append({"id": b.get("id"), "label": b.get("label", ""),
                        "type": vtype, "saved": saved, "current": cur,
                        "addr": addr, "status": status, "why": why,
                        "mode": b.get("mode", "abs")})
    finally:
        if fd is not None:
            os.close(fd)
    with STATE_LOCK:
        STATE["recovered"] = out
    ok = sum(1 for r in out if r["status"] == "ok")
    log(f"收藏自动重定位：{len(out)} 条记录，{ok} 条已恢复")
    return out



# ---------------------------------------------------------------- 会话状态

STATE_LOCK = threading.RLock()
STATE = {
    "pid": None, "game": "", "key": "",
    "vtype": "int32", "mode": "none",
    "candidates": [], "fuzzy": [], "truncated": False,
    "freezes": {}, "log": [], "busy": False, "scan_info": "",
    "recovered": [],
}
FREEZE_THREAD = None
FREEZE_STOP = threading.Event()


def log(msg):
    ts = time.strftime("%H:%M:%S")
    with STATE_LOCK:
        STATE["log"].append(f"[{ts}] {msg}")
        STATE["log"] = STATE["log"][-80:]
    print(msg, flush=True)


def freeze_loop():
    while not FREEZE_STOP.is_set():
        with STATE_LOCK:
            items = list(STATE["freezes"].items())
            pid = STATE["pid"]
        if items and pid:
            try:
                fd = open_mem(pid, write=True)
            except OSError:
                fd = None
            if fd is not None:
                try:
                    for addr, item in items:
                        _write_value_fd(fd, addr, item["type"], item["value"])
                finally:
                    os.close(fd)
        FREEZE_STOP.wait(0.05)


def start_freezer():
    global FREEZE_THREAD
    if FREEZE_THREAD and FREEZE_THREAD.is_alive():
        return
    FREEZE_STOP.clear()
    FREEZE_THREAD = threading.Thread(target=freeze_loop, daemon=True)
    FREEZE_THREAD.start()


def cell_type_for(addr):
    with STATE_LOCK:
        for c in STATE["candidates"]:
            if c["addr"] == addr:
                return c["type"]
        return STATE["vtype"]


# ---------------------------------------------------------------- 网页界面

PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>DeckTrainer</title>
<style>
:root{--bg:#0f1115;--card:#181b21;--line:#2a2f38;--txt:#e8eaf0;--dim:#9aa3b2;--acc:#f5c518;--ok:#39c26b;--bad:#e5534b}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.5 -apple-system,"Segoe UI",Roboto,"PingFang SC","Noto Sans CJK SC",sans-serif;padding:12px 12px 40px}
h1{font-size:18px;margin:0 0 8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px;margin:10px 0}
.card h2{font-size:14px;margin:0 0 8px;color:var(--acc)}
.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
select,input,button{background:#0b0d11;color:var(--txt);border:1px solid var(--line);border-radius:8px;padding:9px 10px;font-size:14px}
button{background:#232833;border-color:#323a47}
button:active{background:#2f3745}
button.pri{background:var(--acc);color:#111;border-color:var(--acc);font-weight:600}
button.gr{background:var(--ok);border-color:var(--ok);color:#08130c}
button.rd{background:#3a2224;border-color:var(--bad);color:#ffb4ae}
input[type=text],input[type=number]{width:110px}
.status{font-size:12px;color:var(--dim)}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:5px 4px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
th{color:var(--dim);font-weight:400}
td input{width:80px;padding:4px 6px;font-size:12px}
td button{padding:4px 6px;font-size:12px}
.mono{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px}
.logbox{max-height:150px;overflow:auto;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;color:var(--dim)}
.tip{font-size:12px;color:var(--dim);line-height:1.7}
.hidden{display:none}
.level{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:4px}
.lv-ok{background:var(--ok)} .lv-bad{background:var(--bad)}
</style></head>
<body>
<h1>DeckTrainer <span class="status" id="stat"></span></h1>

<div class="card">
  <h2>① 附加游戏进程</h2>
  <div class="row">
    <input id="filter" type="text" placeholder="筛选进程名…" oninput="fillProcs()">
    <select id="procs" style="max-width:70vw"></select>
    <button onclick="loadProcs()">刷新</button>
    <button class="pri" onclick="attach()">附加</button>
  </div>
  <div class="status" style="margin-top:6px" id="attachInfo">未附加。Steam/Proton/Wine/原生 Linux 游戏都能在这里选。</div>
</div>

<div class="card">
  <h2>② 搜索数值</h2>
  <div class="row">
    <select id="vtype"></select>
    <input id="value" type="text" placeholder="当前数值" inputmode="decimal">
    <button class="pri" onclick="firstScan('multi')" title="不知道类型就选这个">全类型搜索</button>
    <button onclick="firstScan('exact')">指定类型搜索</button>
    <button onclick="firstScan('fuzzy')" title="没有数字显示的血条用这个">模糊快照(实验)</button>
  </div>
  <div class="row" style="margin-top:8px">
    <span class="status">再次筛选：</span>
    <input id="value2" type="text" placeholder="新数值" inputmode="decimal">
    <button onclick="nextScan('exact')">精确值</button>
    <button onclick="nextScan('inc')">增加</button>
    <button onclick="nextScan('dec')">减少</button>
    <button onclick="nextScan('changed')">有变化</button>
    <button onclick="nextScan('unchanged')">没变化</button>
  </div>
  <div class="status" style="margin-top:8px" id="scanInfo">尚未开始搜索</div>
</div>

<div class="card">
  <h2>③ 结果 <span class="status" id="cnt"></span></h2>
  <div style="overflow:auto;max-height:44vh">
    <table><thead><tr><th>#</th><th>类型</th><th>地址</th><th>当前值</th><th>写入</th><th></th></tr></thead>
    <tbody id="rows"><tr><td colspan="6" class="tip">无结果</td></tr></tbody></table>
  </div>
</div>

<div class="card">
  <h2>④ 已锁定</h2>
  <div id="frz"><div class="tip" id="frzTip">暂无锁定项。锁定后每 50ms 自动回写，等于无限血量/金钱。</div></div>
</div>

<div class="card">
  <h2>⑤ 收藏自动恢复 <button style="float:right;padding:4px 8px;font-size:12px" onclick="relocateAll()">重新恢复</button></h2>
  <div id="rec"><div class="tip">附加游戏后，收藏的数值会按「模块+偏移」自动复原；堆地址会按旧值验证。</div></div>
</div>

<div class="card">
  <h2>⑥ 常用数值收藏</h2>
  <div id="bm"><div class="tip">在结果行点 ★ 保存数值；下次开游戏点「重搜」按旧值快速定位。</div></div>
</div>

<div class="card" id="recipeCard">
  <h2>游戏配方</h2>
  <div id="recipe"></div>
</div>

<div class="card">
  <h2>日志</h2>
  <div class="logbox" id="log"></div>
</div>

<script>
const $=id=>document.getElementById(id);
let PROCS=[];
const TYPES=[["int32","4字节整数"],["uint32","4字节无符号"],["int64","8字节整数"],["uint64","8字节无符号"],["float","单精度小数"],["double","双精度小数"]];
TYPES.forEach(([v,l])=>{const o=document.createElement('option');o.value=v;o.textContent=l;$('vtype').appendChild(o);});

async function api(path,body){
  const r=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});
  return r.json();
}
function fmt(v){ if(v===null||v===undefined) return '？'; if(typeof v==='number'&&!Number.isInteger(v)) return v.toFixed(4); return v; }
function shortType(t){return {"int32":"i32","uint32":"u32","int64":"i64","uint64":"u64","float":"f32","double":"f64"}[t]||t;}

async function loadProcs(){
  const d=await api('/api/processes'); PROCS=d.processes; fillProcs();
}
function fillProcs(){
  const q=($('filter').value||'').toLowerCase();
  const sel=$('procs'); sel.innerHTML='';
  const list=PROCS.filter(p=>!q||p.name.toLowerCase().includes(q)||p.exe.toLowerCase().includes(q)||(''+p.pid).includes(q));
  if(!list.length){const o=document.createElement('option');o.textContent='无匹配进程（先启动游戏）';sel.appendChild(o);return;}
  list.forEach(p=>{const o=document.createElement('option');o.value=p.pid;
    o.textContent=`${p.name} [${p.pid}] ${p.kind} ${(p.rss/1048576).toFixed(0)}MB`;sel.appendChild(o);});
}
async function attach(){
  const pid=+$('procs').value; if(!pid){alert('请选择进程');return;}
  const d=await api('/api/attach',{pid});
  if(d.ok){ $('attachInfo').textContent=`已附加：${d.game} (pid ${d.pid})，可写内存段 ${d.regions} 个`; loadRecipe(d.key); refresh(); }
  else alert(d.error);
}
async function firstScan(mode){
  let vtype=null, value=null;
  if(mode!=='fuzzy'){ value=$('value').value.trim(); if(value===''){alert('请输入当前数值');return;} }
  if(mode==='exact') vtype=$('vtype').value;
  const d=await api('/api/scan',{mode,vtype,value});
  if(!d.ok){alert(d.error);return;}
  $('scanInfo').textContent=d.info; refresh();
}
async function nextScan(op){
  let value=null;
  if(op==='exact'){ value=$('value2').value.trim(); if(value===''){alert('请输入新数值');return;} }
  const d=await api('/api/next',{op,value});
  if(!d.ok){alert(d.error);return;}
  $('scanInfo').textContent=d.info; refresh();
}
async function write(addr,type){
  const inp=document.querySelector(`input[data-addr="${addr}"]`);
  const value=inp.value.trim();
  const d=await api('/api/write',{addr:+addr,value,type});
  if(!d.ok) alert(d.error); else { inp.value=''; refresh(); }
}
async function freeze(addr,val,type){
  const d=await api('/api/freeze',{addr:+addr,value:val,type,on:true});
  if(!d.ok) alert(d.error); refresh();
}
async function unfreeze(addr){ await api('/api/freeze',{addr:+addr,on:false}); refresh(); }
async function bookmark(addr,type,val){
  const label=prompt('给这个数值起个名字（如：残尘/生命）：','');
  if(!label) return;
  const d=await api('/api/bookmark',{action:'save',addr:+addr,type,value:val,label});
  if(!d.ok) alert(d.error); refresh();
}
async function bmRescan(id){ const d=await api('/api/bookmark',{action:'rescan',id});
  if(!d.ok) alert(d.error); else $('scanInfo').textContent=d.info; refresh(); }
async function bmDelete(id){ await api('/api/bookmark',{action:'delete',id}); refresh(); }
async function relocateAll(){ const d=await api('/api/bookmark',{action:'relocate'});
  if(!d.ok) alert(d.error); else $('scanInfo').textContent=d.info; refresh(); }
async function lockRecovered(addr,type,val){ const d=await api('/api/freeze',{addr:+addr,type,value:val,on:true});
  if(!d.ok) alert(d.error); refresh(); }

async function refresh(){
  const d=await api('/api/state');
  $('stat').textContent = d.pid? `● ${d.game}` : '未附加';
  $('cnt').textContent = d.count? `候选 ${d.count} 个` : '';
  const tb=$('rows');
  if(!d.rows.length){ tb.innerHTML='<tr><td colspan="6" class="tip">无结果</td></tr>'; }
  else {
    tb.innerHTML=d.rows.map((r,i)=>`<tr><td>${i+1}</td><td class="status">${shortType(r.type)}</td>
      <td class="mono">0x${r.addr.toString(16)}</td><td>${fmt(r.value)}</td>
      <td><input data-addr="${r.addr}" placeholder="新值"> <button class="gr" onclick="write('${r.addr}')">写入</button></td>
      <td><button onclick="freeze('${r.addr}',${r.value},'${r.type}')">锁定</button>
      <button onclick="bookmark('${r.addr}','${r.type}',${r.value})">★</button></td></tr>`).join('');
  }
  const fr=d.freezes;
  $('frzTip').style.display = fr.length? 'none':'block';
  $('frz').innerHTML = fr.length? `<table>${fr.map(f=>`<tr><td class="mono">0x${f.addr.toString(16)}</td><td>${shortType(f.type)}</td><td>${fmt(f.value)}</td><td><button class="rd" onclick="unfreeze('${f.addr}')">解除</button></td></tr>`).join('')}</table>`:'';
  const bms=d.bookmarks||[];
  $('bm').innerHTML = bms.length? `<table>${bms.map(b=>`<tr><td>${b.label}</td><td class="status">${b.game||''}</td><td class="status">${shortType(b.type)}</td><td>${fmt(b.value)}</td><td><button onclick="bmRescan('${b.id}')">重搜</button> <button class="rd" onclick="bmDelete('${b.id}')">删除</button></td></tr>`).join('')}</table>`:'<div class="tip">在结果行点 ★ 保存数值；下次开游戏点「重搜」按旧值快速定位。</div>';
  const rec=d.recovered||[];
  if(!rec.length){ $('rec').innerHTML='<div class="tip">附加游戏后，收藏的数值会按「模块+偏移」自动复原；堆地址会按旧值验证。</div>'; }
  else {
    $('rec').innerHTML=`<table>${rec.map(r=>{const st=r.status==='ok'?'已恢复':(r.status==='mismatch'?'值已变':'失效');
      const col=r.status==='ok'?'var(--ok)':(r.status==='mismatch'?'#e8b34b':'var(--bad)');
      return `<tr><td>${r.label}</td><td style="color:${col}">${st}</td><td class="status">${shortType(r.type)}</td>
      <td class="mono">${r.addr?('0x'+r.addr.toString(16)):'-'}</td><td>${fmt(r.current)}</td>
      <td>${r.addr?`<button class="gr" onclick="lockRecovered('${r.addr}','${r.type}',${r.current})">锁定</button>`:''}
      <button onclick="bmRescan('${r.id}')">重搜</button></td></tr>`}).join('')}</table>`;
  }
  $('log').textContent=d.log.join('\n');
}
function loadRecipe(key){
  api('/api/recipe?key='+encodeURIComponent(key||'')).then(d=>{
    if(!d.recipe){$('recipeCard').classList.add('hidden');return;}
    $('recipeCard').classList.remove('hidden');
    $('recipe').innerHTML=`<div class="tip"><b>${d.recipe.name}</b>：${d.recipe.tip}</div>
      <table style="margin-top:6px">${d.recipe.values.map(v=>`<tr><td>${v.label}</td><td class="status">${v.type.toUpperCase()}</td><td class="tip">${v.how}</td></tr>`).join('')}</table>`;
  });
}
loadProcs(); refresh(); setInterval(refresh,1200);
</script>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "DeckTrainer"

    def log_message(self, fmt, *args):
        pass

    def _send(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/processes":
            self._send({"processes": list_processes()})
            return
        if parsed.path == "/api/recipe":
            key = (parse_qs(parsed.query).get("key") or [""])[0]
            recipe = next((g for g in GAMES if g["key"] == key), None)
            self._send({"recipe": recipe})
            return
        if parsed.path == "/api/state":
            self._state()
            return
        self._send({"error": "not found"}, 404)

    def _state(self):
        with STATE_LOCK:
            candidates = STATE["candidates"]
            pid = STATE["pid"]
        rows = read_cells(pid, candidates, 150) if (pid and candidates) else []
        with STATE_LOCK:
            freezes = [{"addr": a, **i} for a, i in STATE["freezes"].items()]
            log_tail = STATE["log"][-40:]
            count = len(candidates)
            game = STATE["game"]
            mode = STATE["mode"]
            recovered = STATE.get("recovered", [])
        self._send({"pid": pid, "game": game, "count": count, "rows": rows,
                    "freezes": freezes, "log": log_tail, "mode": mode,
                    "recovered": recovered,
                    "bookmarks": load_bookmarks()[-30:]})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._send({"ok": False, "error": "bad request"}, 400)
            return
        path = urlparse(self.path).path
        try:
            if path == "/api/attach":
                self._attach(int(data["pid"]))
            elif path == "/api/scan":
                self._scan(data)
            elif path == "/api/next":
                self._next(data)
            elif path == "/api/write":
                self._write(data)
            elif path == "/api/freeze":
                self._freeze(data)
            elif path == "/api/bookmark":
                self._bookmark(data)
            else:
                self._send({"ok": False, "error": "not found"}, 404)
        except Exception as e:
            self._send({"ok": False, "error": f"{type(e).__name__}: {e}"})

    def _attach(self, pid):
        procs = {p["pid"]: p for p in list_processes()}
        p = procs.get(pid)
        regions = list_regions(pid)
        if not regions:
            self._send({"ok": False, "error": "无法读取该进程内存：确认游戏已进入场景，或桌面模式请用 start.sh（sudo）运行"})
            return
        with STATE_LOCK:
            STATE.update({"pid": pid, "game": p["name"] if p else f"pid {pid}",
                          "key": p["key"] if p else "",
                          "candidates": [], "fuzzy": [], "mode": "none",
                          "freezes": {}, "truncated": False})
        start_freezer()
        log(f"已附加 {STATE['game']} (pid {pid})，可写段 {len(regions)}")
        recovered = relocate_bookmarks(pid, STATE["game"])
        self._send({"ok": True, "pid": pid, "game": STATE["game"],
                    "regions": len(regions), "key": STATE["key"],
                    "recovered": recovered})

    def _scan(self, data):
        with STATE_LOCK:
            pid = STATE["pid"]
        if not pid:
            self._send({"ok": False, "error": "请先附加游戏进程"})
            return
        mode = data.get("mode", "exact")
        regions = list_regions(pid)
        t0 = time.time()
        if mode in ("exact", "multi"):
            raw = (data.get("value") or "").strip()
            if raw == "":
                self._send({"ok": False, "error": "请输入数值"})
                return
            if mode == "exact":
                vtype = data.get("vtype", "int32")
                if vtype not in TYPE_SPECS:
                    self._send({"ok": False, "error": "未知数值类型"})
                    return
                try:
                    value = coerce_value(vtype, raw)
                except ValueError:
                    self._send({"ok": False, "error": "数值格式错误"})
                    return
                log(f"精确搜索 {value}（{TYPE_LABEL[vtype]}）…")
                addrs = scan_exact(pid, regions, vtype, value)
                cells = [{"addr": a, "type": vtype, "value": value} for a in addrs]
                msg = f"精确搜索({TYPE_LABEL[vtype]})：{len(cells)} 个候选"
            else:
                try:
                    float(raw)
                except ValueError:
                    self._send({"ok": False, "error": "数值格式错误"})
                    return
                log(f"全类型搜索 {raw} …")
                cells = scan_multi(pid, regions, raw)
                by_type = {}
                for c in cells:
                    by_type[c["type"]] = by_type.get(c["type"], 0) + 1
                detail = "、".join(f"{k}:{v}" for k, v in by_type.items())
                msg = f"全类型搜索：{len(cells)} 个候选" + (f"（{detail}）" if detail else "")
            with STATE_LOCK:
                STATE.update({"mode": "exact", "candidates": cells,
                              "fuzzy": [], "truncated": len(cells) >= MAX_MATCHES})
                STATE["vtype"] = cells[0]["type"] if cells else STATE["vtype"]
            info = f"{msg}（{time.time()-t0:.1f}s）"
            if len(cells) >= MAX_MATCHES:
                info += "，已达上限，建议换更独特的数值"
        elif mode == "fuzzy":
            vtype = data.get("vtype", "int32")
            if vtype not in TYPE_SPECS:
                self._send({"ok": False, "error": "请选择模糊搜索的数值类型"})
                return
            log(f"模糊快照（{TYPE_LABEL[vtype]}）…")
            snaps = fuzzy_snapshot(pid, regions, vtype)
            with STATE_LOCK:
                STATE.update({"vtype": vtype, "mode": "fuzzy", "fuzzy": snaps,
                              "candidates": [], "truncated": False})
            info = (f"已快照 {len(snaps)} 个内存段。回游戏改变数值后，"
                    f"点『减少/增加/有变化』筛选（{time.time()-t0:.1f}s）")
        else:
            self._send({"ok": False, "error": "未知搜索模式"})
            return
        with STATE_LOCK:
            STATE["scan_info"] = info
            count = len(STATE["candidates"])
        log(info)
        self._send({"ok": True, "info": info, "count": count})

    def _next(self, data):
        with STATE_LOCK:
            pid, mode = STATE["pid"], STATE["mode"]
            fuzzy, cells = STATE["fuzzy"], STATE["candidates"]
        if not pid:
            self._send({"ok": False, "error": "请先附加游戏进程"})
            return
        op = data.get("op")
        if op not in ("exact", "inc", "dec", "changed", "unchanged"):
            self._send({"ok": False, "error": "未知筛选方式"})
            return
        t0 = time.time()
        if mode == "fuzzy" and fuzzy:
            if op == "exact":
                self._send({"ok": False, "error": "模糊模式请用『增加/减少/有变化/没变化』"})
                return
            log(f"模糊筛选：{op} …")
            new, truncated = fuzzy_refine(pid, fuzzy, op)
            with STATE_LOCK:
                STATE["candidates"] = new
                STATE["fuzzy"] = []
                STATE["mode"] = "exact"
                STATE["truncated"] = truncated
            count = len(new)
            info = f"筛选后剩 {count} 个候选" + ("（已达上限，请继续筛选）" if truncated else "")
        else:
            if not cells:
                self._send({"ok": False, "error": "当前没有候选，请先搜索"})
                return
            raw = (data.get("value") or "").strip() if op == "exact" else None
            if op == "exact" and raw == "":
                self._send({"ok": False, "error": "请输入新数值"})
                return
            log(f"候选筛选：{op} …（原 {len(cells)} 个）")
            new = refine_cells(pid, cells, op, raw)
            truncated = len(new) > CAND_CAP
            new = new[:CAND_CAP]
            with STATE_LOCK:
                STATE["candidates"] = new
                STATE["truncated"] = truncated
            count = len(new)
            info = f"筛选后剩 {count} 个候选（{(time.time()-t0):.1f}s）"
        log(info)
        self._send({"ok": True, "info": info, "count": count})

    def _write(self, data):
        with STATE_LOCK:
            pid = STATE["pid"]
        if not pid:
            self._send({"ok": False, "error": "请先附加游戏进程"})
            return
        addr = int(data["addr"])
        vtype = data.get("type") or cell_type_for(addr)
        value = coerce_value(vtype, data.get("value"))
        if value is None:
            self._send({"ok": False, "error": "请输入新值"})
            return
        fd = open_mem(pid, write=True)
        try:
            ok = _write_value_fd(fd, addr, vtype, value)
        finally:
            os.close(fd)
        log(f"写入 0x{addr:x} ← {value} ({vtype}) {'成功' if ok else '失败'}")
        with STATE_LOCK:
            for c in STATE["candidates"]:
                if c["addr"] == addr:
                    c["value"] = value
                    break
        self._send({"ok": ok, "error": "" if ok else "写入失败，地址可能已失效"})

    def _freeze(self, data):
        with STATE_LOCK:
            pid = STATE["pid"]
        if not pid:
            self._send({"ok": False, "error": "请先附加游戏进程"})
            return
        addr = int(data["addr"])
        if not data.get("on", True):
            with STATE_LOCK:
                STATE["freezes"].pop(addr, None)
            self._send({"ok": True})
            return
        vtype = data.get("type") or cell_type_for(addr)
        value = coerce_value(vtype, data.get("value"))
        if value is None:
            self._send({"ok": False, "error": "需要锁定值"})
            return
        with STATE_LOCK:
            STATE["freezes"][addr] = {"type": vtype, "value": value}
        start_freezer()
        log(f"锁定 0x{addr:x} = {value} ({vtype})")
        self._send({"ok": True})

    def _bookmark(self, data):
        action = data.get("action")
        items = load_bookmarks()
        if action == "save":
            with STATE_LOCK:
                game = STATE["game"]
                pid = STATE["pid"]
            addr = int(data.get("addr", 0))
            vtype = data.get("type", "int32")
            info = module_of(pid, addr) if pid else None
            entry = {
                "id": str(int(time.time() * 1000)),
                "game": game,
                "label": (data.get("label") or "未命名")[:40],
                "type": vtype,
                "value": data.get("value"),
                "addr": addr,
                "mode": "module" if info else "abs",
                "module": info[0] if info else "",
                "offset": info[2] if info else 0,
                "ts": time.strftime("%Y-%m-%d %H:%M"),
            }
            items.append(entry)
            save_bookmarks(items[-100:])
            hint = (f"已收藏（模块偏移定位，下次启动可自动恢复）" if info
                    else "已收藏（堆地址，下次启动尝试自动验证）")
            log(f"收藏『{entry['label']}』{vtype} {hint}")
            self._send({"ok": True, "mode": entry["mode"]})
        elif action == "delete":
            bid = str(data.get("id"))
            save_bookmarks([b for b in items if b.get("id") != bid])
            self._send({"ok": True})
        elif action == "rescan":
            bid = str(data.get("id"))
            bm = next((b for b in items if b.get("id") == bid), None)
            if not bm:
                self._send({"ok": False, "error": "收藏不存在"})
                return
            with STATE_LOCK:
                pid = STATE["pid"]
            if not pid:
                self._send({"ok": False, "error": "请先附加游戏进程"})
                return
            vtype = bm.get("type", "int32")
            try:
                value = coerce_value(vtype, bm.get("value"))
            except (TypeError, ValueError):
                self._send({"ok": False, "error": "收藏的数值无效"})
                return
            regions = list_regions(pid)
            log(f"收藏重搜：{bm['label']} = {value} ({vtype})")
            addrs = scan_exact(pid, regions, vtype, value)
            cells = [{"addr": a, "type": vtype, "value": value} for a in addrs]
            with STATE_LOCK:
                STATE.update({"candidates": cells, "mode": "exact",
                              "fuzzy": [], "vtype": vtype})
            info = f"『{bm['label']}』重搜：{len(cells)} 个候选（原值 {value}）"
            if not cells:
                info = f"『{bm['label']}』重搜：0 个候选，数值可能已变化，请手动搜当前值"
            log(info)
            self._send({"ok": True, "info": info, "count": len(cells)})
        elif action == "relocate":
            with STATE_LOCK:
                pid, game = STATE["pid"], STATE["game"]
            if not pid:
                self._send({"ok": False, "error": "请先附加游戏进程"})
                return
            recovered = relocate_bookmarks(pid, game)
            self._send({"ok": True, "recovered": recovered,
                        "info": f"自动重定位完成：{sum(1 for r in recovered if r['status']=='ok')}/{len(recovered)} 条已恢复"})
        else:
            self._send({"ok": False, "error": "未知操作"})


# ---------------------------------------------------------------- 入口

def lan_ips():
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    return ips


def selftest(pid, value, vtype="int32"):
    regions = list_regions(pid)
    print(f"目标 pid={pid}，可写段 {len(regions)} 个")
    addrs = scan_exact(pid, regions, vtype, value)
    print(f"精确扫描 {value}: {len(addrs)} 个地址")
    assert addrs
    fd = open_mem(pid, write=True)
    first = addrs[0]
    old = _read_value_fd(fd, first, vtype)
    new = value + 1 if vtype not in ("float", "double") else value + 1.5
    ok = _write_value_fd(fd, first, vtype, new)
    got = _read_value_fd(fd, first, vtype)
    print(f"写入 {old} → {new}: ok={ok}, 读回={got}")
    assert got == new
    os.close(fd)
    multi = scan_multi(pid, regions, str(value))
    print(f"全类型扫描：{len(multi)} 个（类型 {sorted({c['type'] for c in multi})}）")
    snaps = fuzzy_snapshot(pid, regions, vtype)
    print(f"模糊快照：{len(snaps)} 段")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=PORT_DEFAULT)
    ap.add_argument("--lan", action="store_true", help="允许局域网访问（手机控制用）")
    ap.add_argument("--launch", nargs=argparse.REMAINDER, help="先启动游戏命令再提供服务")
    ap.add_argument("--selftest", nargs=2, metavar=("PID", "VALUE"))
    args = ap.parse_args()

    if args.selftest:
        v = float(args.selftest[1]) if "." in args.selftest[1] else int(args.selftest[1])
        selftest(int(args.selftest[0]), v)
        return

    host = "0.0.0.0" if args.lan else "127.0.0.1"
    server = ThreadingHTTPServer((host, args.port), Handler)
    urls = [f"http://127.0.0.1:{args.port}"]
    if args.lan:
        urls += [f"http://{ip}:{args.port}" for ip in lan_ips()]
    print("=" * 52)
    print(" DeckTrainer v2 已启动，浏览器打开以下地址：")
    for u in urls:
        print("   " + u)
    if args.launch:
        print(" 手机与 Deck 连同一个 Wi-Fi 时用上面 192.168.x.x 的地址")
    print("=" * 52, flush=True)
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_url.txt"), "w") as f:
            f.write("\n".join(urls) + "\n")
    except OSError:
        pass

    proc = None
    if args.launch:
        launch = [a for a in args.launch if a != "--"]
        print("启动游戏:", " ".join(launch), flush=True)
        proc = subprocess.Popen(launch)

        def wait_game():
            proc.wait()
            time.sleep(0.5)
            print("游戏已退出，修改器随之关闭", flush=True)
            os.kill(os.getpid(), 15)

        threading.Thread(target=wait_game, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
