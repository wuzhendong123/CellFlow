"""DSL 静态校验、Schema 推导与 DAG 执行（TECH_DESIGN §6.1 静态校验 8 条、§6.2、§7.9）。"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field

from cellflow.engine.dataset import Dataset
from cellflow.engine.grid import Workbook
from cellflow.engine.nodes import (
    REGISTRY,
    FatalNodeError,
    InferResult,
    PortSchema,
    RunContext,
    input_ports,
    output_ports,
)
from cellflow.engine.source import run_source

DSL_VERSION = "1.0"


@dataclass
class Analysis:
    errors: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    schemas: dict[tuple[str, str], PortSchema] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_json(self) -> dict:
        ports = defaultdict(dict)
        for (n, p), s in self.schemas.items():
            ports[n][p] = s.to_json()
        return {"ok": self.ok, "errors": self.errors, "warnings": self.warnings, "ports": ports}


def _nodes(dsl: dict) -> dict[str, dict]:
    return {n["id"]: n for n in dsl.get("nodes", [])}


def toposort(dsl: dict) -> tuple[list[str], list[str]]:
    """返回 (拓扑序, 成环节点)。"""
    nodes = _nodes(dsl)
    indeg = {n: 0 for n in nodes}
    succ = defaultdict(set)
    for e in dsl.get("edges", []):
        s, t = e["source"]["nodeId"], e["target"]["nodeId"]
        if s in nodes and t in nodes and t not in succ[s]:
            succ[s].add(t)
            indeg[t] += 1
    order = []
    ready = sorted(n for n, d in indeg.items() if d == 0)
    while ready:
        n = ready.pop(0)
        order.append(n)
        for t in sorted(succ[n]):
            indeg[t] -= 1
            if indeg[t] == 0:
                ready.append(t)
    cyc = [n for n in nodes if n not in order]
    return order, cyc


def analyze(dsl: dict) -> Analysis:
    """保存时的静态校验 + 每个端口的 Schema 推导。"""
    a = Analysis()
    nodes = _nodes(dsl)
    if len(nodes) != len(dsl.get("nodes", [])):
        a.errors.append({"code": "DSL_INVALID", "message": "节点 ID 重复"})
    for n in nodes.values():
        if n.get("type") not in REGISTRY:
            a.errors.append({"code": "DSL_INVALID", "message": f"不支持的节点类型 {n.get('type')}", "node": n["id"]})
    # 1. 连线合法性、无环、端口约束
    incoming: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for e in dsl.get("edges", []):
        s, sp = e["source"]["nodeId"], e["source"]["portId"]
        t, tp = e["target"]["nodeId"], e["target"]["portId"]
        if s not in nodes or t not in nodes:
            a.errors.append({"code": "DSL_INVALID", "message": f"连线 {e.get('id')} 指向不存在的节点"})
            continue
        if sp not in output_ports(nodes[s]):
            a.errors.append({"code": "DSL_INVALID", "message": f"连线 {e.get('id')}：节点「{nodes[s].get('label', s)}」没有输出端口 {sp}", "node": s})
            continue
        ins = input_ports(nodes[t])
        if nodes[t]["type"] == "EXCEL_SOURCE":
            a.errors.append({"code": "DSL_INVALID", "message": "源节点没有输入端口，不能连线到源节点", "node": t})
            continue
        if tp not in ins:
            a.errors.append({"code": "DSL_INVALID", "message": f"连线 {e.get('id')}：节点「{nodes[t].get('label', t)}」没有输入端口 {tp}", "node": t})
            continue
        incoming[(t, tp)].append(e)
    order, cyc = toposort(dsl)
    if cyc:
        a.errors.append({"code": "DSL_CYCLE", "message": "连线形成了环：" + "、".join(cyc)})
    a.order = order
    for n in nodes.values():
        for p, spec in input_ports(n).items():
            es = incoming.get((n["id"], p), [])
            if not es and not spec.get("optional"):
                label = "引用输入" if spec.get("kind") == "REF" else "输入端口"
                a.errors.append({"code": "PORT_NOT_CONNECTED", "message": f"节点「{n.get('label', n['id'])}」的{label} {p} 未连线", "node": n["id"]})
            if len(es) > 1 and not spec.get("multi"):
                a.errors.append({"code": "DSL_INVALID", "message": f"节点「{n.get('label', n['id'])}」的端口 {p} 只能接一条线", "node": n["id"]})
    # SINK 数据集名唯一
    ds_names = [n.get("config", {}).get("dataset") for n in nodes.values() if n.get("type") == "SINK"]
    for d in {x for x in ds_names if ds_names.count(x) > 1}:
        a.errors.append({"code": "DSL_INVALID", "message": f"数据集名「{d}」重复"})
    tables = [((n.get("config") or {}).get("binding") or {}).get("table") for n in nodes.values() if n.get("type") == "SINK"]
    for t in {x for x in tables if x and tables.count(x) > 1}:
        a.errors.append({"code": "DSL_INVALID", "message": f"目标表「{t}」被多个输出节点使用"})
    # 2~8. 按拓扑序推导 Schema
    for nid in order:
        n = nodes[nid]
        infer, _ = REGISTRY.get(n.get("type"), (None, None))
        if infer is None:
            continue
        ins: dict = {}
        for p, spec in input_ports(n).items():
            srcs = [a.schemas.get((e["source"]["nodeId"], e["source"]["portId"])) for e in incoming.get((nid, p), [])]
            srcs = [s for s in srcs if s is not None]
            if not srcs:
                continue
            if spec.get("kind") == "PARAM" and not all(s.single_row for s in srcs):
                a.errors.append({"code": "PARAM_NOT_SINGLE_ROW", "message": f"节点「{n.get('label', nid)}」的参数端口只能接入单行数据（如键值区、汇总区）", "node": nid})
            ins[p] = srcs if spec.get("multi") else srcs[0]
        r: InferResult = infer(n, ins)
        for e in r.errors:
            a.errors.append({**e, "node": nid})
        for w in r.warnings:
            a.warnings.append({**w, "node": nid})
        for p, s in r.outputs.items():
            a.schemas[(nid, p)] = s
    # VALIDATOR 引用输入别名与端口一一对应
    for n in nodes.values():
        if n.get("type") == "VALIDATOR":
            refs = (n.get("config") or {}).get("refs") or {}
            for alias, port in refs.items():
                if not port.startswith("in_ref_"):
                    a.errors.append({"code": "DSL_INVALID", "message": f"引用输入「{alias}」的端口名必须以 in_ref_ 开头", "node": n["id"]})
    return a


def reference_source_ports(dsl: dict) -> set[tuple[str, str]]:
    """被引用输入 / 关联右侧 / 查表字典 / 参数端口消费的上游源端口：预览时不采样（C6）。"""
    nodes = _nodes(dsl)
    edges = dsl.get("edges", [])
    ref_targets = set()
    for n in nodes.values():
        for p, spec in input_ports(n).items():
            if spec.get("reference") or spec.get("kind") in ("PARAM", "REF"):
                ref_targets.add((n["id"], p))
    into: dict[str, list[dict]] = defaultdict(list)
    for e in edges:
        into[e["target"]["nodeId"]].append(e)
    out: set[tuple[str, str]] = set()
    stack = [e for e in edges if (e["target"]["nodeId"], e["target"]["portId"]) in ref_targets]
    seen = set()
    while stack:
        e = stack.pop()
        key = (e["source"]["nodeId"], e["source"]["portId"])
        if key in seen:
            continue
        seen.add(key)
        src = nodes.get(e["source"]["nodeId"])
        if src is None:
            continue
        if src["type"] == "EXCEL_SOURCE":
            out.add(key)
        else:
            stack += into.get(src["id"], [])
    return out


def ancestors(dsl: dict, node_id: str) -> set[str]:
    into = defaultdict(list)
    for e in dsl.get("edges", []):
        into[e["target"]["nodeId"]].append(e["source"]["nodeId"])
    seen, stack = set(), [node_id]
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        stack += into.get(n, [])
    return seen


@dataclass
class RunResult:
    outputs: dict[tuple[str, str], Dataset] = field(default_factory=dict)
    issues: list[dict] = field(default_factory=list)
    metrics: dict[str, dict] = field(default_factory=dict)
    locate_report: list[dict] = field(default_factory=list)
    sinks: dict[str, tuple[dict, Dataset]] = field(default_factory=dict)  # dataset → (sink 节点, 数据)
    skipped: list[str] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i["severity"] == "ERROR")

    @property
    def warn_count(self) -> int:
        return sum(1 for i in self.issues if i["severity"] == "WARN")


def execute(dsl: dict, wb: Workbook, settings: dict, sample_rows: int | None = None, until_node: str | None = None) -> RunResult:
    """按拓扑序执行；结构性错误只跳过下游，其他分支继续（§7.9）。"""
    res = RunResult()
    analysis = analyze(dsl)
    if not analysis.ok:
        for e in analysis.errors:
            res.issues.append({"node": e.get("node", ""), "severity": "ERROR", "code": e["code"], "message": e["message"]})
        return res
    nodes = _nodes(dsl)
    keep = ancestors(dsl, until_node) if until_node else set(nodes)
    no_sample = reference_source_ports(dsl) if sample_rows else set()
    failed_ports: set[tuple[str, str]] = set()
    incoming = defaultdict(list)
    for e in dsl.get("edges", []):
        incoming[(e["target"]["nodeId"], e["target"]["portId"])].append(e)
    for nid in analysis.order:
        if nid not in keep:
            continue
        n = nodes[nid]
        t0 = time.perf_counter()
        if n["type"] == "EXCEL_SOURCE":
            sr = run_source(n.get("config") or {}, wb, nid)
            res.issues += sr.issues
            res.locate_report += [dict(r, node=nid) for r in sr.locate_report]
            for port, ds in sr.outputs.items():
                if sample_rows and (nid, port) not in no_sample:
                    ds = ds.head(sample_rows)
                res.outputs[(nid, port)] = ds
            failed_ports |= {(nid, p) for p in sr.failed_ports}
            res.metrics[nid] = {"rows": {p: len(d) for p, d in sr.outputs.items()}, "ms": round((time.perf_counter() - t0) * 1000, 1)}
            continue
        ins: dict = {}
        blocked = False
        for p, spec in input_ports(n).items():
            es = incoming.get((nid, p), [])
            vals = []
            for e in es:
                key = (e["source"]["nodeId"], e["source"]["portId"])
                if key in failed_ports or key not in res.outputs:
                    blocked = True
                    break
                vals.append(res.outputs[key])
            if blocked:
                break
            if vals:
                ins[p] = vals if spec.get("multi") else vals[0]
        if blocked:
            res.skipped.append(nid)
            res.issues.append({"node": nid, "severity": "INFO", "code": "NODE_SKIPPED", "message": "上游节点失败，本节点已跳过"})
            failed_ports |= {(nid, p) for p in output_ports(n)}
            continue
        ctx = RunContext(nid, settings)
        _, run = REGISTRY[n["type"]]
        try:
            produced = run(n, ins, ctx)
        except FatalNodeError as e:
            res.issues += ctx.issues
            issue = {"node": nid, "severity": "ERROR", "code": e.code, "message": e.message}
            if isinstance(e.data, dict) and e.data.get("cells"):
                issue["cell"] = e.data["cells"][0]
                issue["related"] = e.data["cells"]
            if e.data is not None:
                issue["data"] = e.data
            res.issues.append(issue)
            failed_ports |= {(nid, p) for p in output_ports(n)}
            res.metrics[nid] = {"failed": True, "ms": round((time.perf_counter() - t0) * 1000, 1)}
            continue
        res.issues += ctx.issues
        for p, d in produced.items():
            res.outputs[(nid, p)] = d
        if n["type"] == "SINK":
            ds = ins.get("in")
            res.sinks[(n.get("config") or {}).get("dataset", nid)] = (n, ds)
        res.metrics[nid] = {"rows": {p: len(d) for p, d in produced.items()}, "ms": round((time.perf_counter() - t0) * 1000, 1)}
    return res
