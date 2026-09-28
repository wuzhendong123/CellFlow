import {
  addEdge,
  Background,
  Connection,
  Controls,
  Edge,
  Handle,
  MiniMap,
  Node,
  NodeProps,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { App, Button, Dropdown, Radio, Select, Space, Tag, Tooltip, Upload } from "antd";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, get, post, put, upload } from "../../api";
import { canConnect, defaultConfig, Dsl, DslNode, inputPorts, newId, NODE_META, NodeType, outputPorts, RangeJson, REGION_COLORS } from "../../dsl";
import { navigate, setLeaveGuard } from "../../router";
import NodeConfig, { Analysis } from "./NodeConfig";
import RegionPanel from "./RegionPanel";
import ResultPanel from "./ResultPanel";
import SheetPane, { Overlay } from "./SheetPane";

type Layout = "canvas" | "lr" | "tb";
const LAYOUT_KEY = "cellflow.layout";
const CONFIG_WIDTH_KEY = "cellflow.configWidth";
const CONFIG_WIDTH = 440;

/** 上手指引：按方案当前状态列出下一步，每步一个直接可点的动作。 */
function GettingStarted({ dsl, fileName, job, fresh, published, onAddSource, onAddSink, onPreview, onSelect }: any) {
  const srcs = dsl.nodes.filter((n: DslNode) => n.type === "EXCEL_SOURCE");
  const sinks = dsl.nodes.filter((n: DslNode) => n.type === "SINK");
  const regions = srcs.reduce((a: number, n: DslNode) => a + (n.config.regions || []).length, 0);
  const unbound = sinks.find((n: DslNode) => !n.config.binding?.table);
  const steps = [
    { done: !!fileName, title: "上传样例文件", tip: "点左上角「上传 / 更换」，用一份真实的 Excel 作为配置样例。" },
    { done: regions > 0, title: "圈选表格区域", tip: "打开 Excel 源的分屏，点「自动识别」一键切出所有表格，再逐个调整形态和字段；也可以手工框选。",
      action: <Button size="small" onClick={onAddSource}>{srcs.length ? "打开圈选" : "添加 Excel 源并圈选"}</Button> },
    { done: dsl.nodes.some((n: DslNode) => !["EXCEL_SOURCE", "SINK"].includes(n.type)), optional: true, title: "加工数据（可选）",
      tip: "从左侧点击添加过滤、派生列、关联、校验等节点，从上游节点右侧的圆点拖线到它的输入端口。选中任意节点可在右侧看到它的输出预览。" },
    { done: sinks.length > 0 && !unbound, title: "输出到业务表", tip: "添加「输出到业务表」节点，连上数据，在右侧「绑定目标表」里选表、映射字段、选主键。",
      action: unbound ? <Button size="small" onClick={() => onSelect(unbound.id)}>去绑定</Button> : sinks.length ? null : <Button size="small" onClick={onAddSink}>添加输出节点</Button> },
    { done: fresh && !(job?.issues?.error > 0), title: "预览检查结果", tip: "点「预览」用样例文件跑一遍；问题会列在下方，点击可定位到 Excel 单元格。",
      action: <Button size="small" onClick={onPreview}>预览</Button> },
    { done: published, title: "发布", tip: "右上角「发布…」，之后业务服务就能通过 Open API 提交文件了。" },
  ];
  const next = steps.findIndex((s) => !s.done && !s.optional);
  return (
    <div id="getting-started" style={{ marginTop: 12 }}>
      <h4 style={{ marginBottom: 6 }}>上手指引</h4>
      {steps.map((s, i) => (
        <div key={s.title} style={{ display: "flex", gap: 8, padding: "6px 8px", marginBottom: 4, borderRadius: 4, background: i === next ? "#e6f4ff" : undefined }}>
          <span style={{ width: 18, color: s.done ? "#52c41a" : "#999" }}>{s.done ? "✓" : i + 1}</span>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: i === next ? 600 : 400 }}>{s.title}</div>
            {(i === next || (!s.done && s.optional)) && <div className="cf-muted">{s.tip}</div>}
            {i === next && s.action && <div style={{ marginTop: 4 }}>{s.action}</div>}
          </div>
        </div>
      ))}
      <div className="cf-muted" style={{ marginTop: 6 }}>表达式怎么写：选中派生列 / 过滤 / 校验节点，点表达式框下方的「语法帮助」；上方「可用字段」可点击插入。</div>
    </div>
  );
}

/** 影响计算结果的部分（不含节点位置），用于判断试跑结果是否过期。 */
function dslSig(d: Dsl | null | undefined): string {
  if (!d) return "";
  return JSON.stringify([d.nodes.map((n) => [n.id, n.type, n.config]), d.edges.map((e) => [e.source, e.target])]);
}

function loadLayout(): { mode: Layout; ratio: number } {
  try {
    const saved = JSON.parse(localStorage.getItem(LAYOUT_KEY) || "{}");
    const mode: Layout = ["canvas", "lr", "tb"].includes(saved.mode) ? saved.mode : "canvas";
    return { mode, ratio: typeof saved.ratio === "number" ? saved.ratio : 0.6 };
  } catch {
    return { mode: "canvas", ratio: 0.6 };
  }
}

interface NodeData extends Record<string, unknown> {
  node: DslNode;
  errors: number;
  warns: number;
  rows?: Record<string, number>;
  invalid: boolean;
  flash: boolean;
  onMenu: (key: string, id: string) => void;
}

function CfNode({ data, selected }: NodeProps<Node<NodeData>>) {
  const n = data.node;
  const ins = inputPorts(n);
  const outs = outputPorts(n);
  const dataIns = ins.filter((p) => p.kind === "DATA");
  const topIns = ins.filter((p) => p.kind !== "DATA");
  const mainOuts = outs.filter((p) => !p.side);
  const sideOuts = outs.filter((p) => p.side);
  const rows = Math.max(dataIns.length, mainOuts.length, 1);
  return (
    <Dropdown trigger={["contextMenu"]} menu={{
      items: [{ key: "copy", label: "复制" }, { key: "delete", label: "删除" }, { key: "output", label: "查看此节点输出" }, { key: "until", label: "只试跑到这里" }],
      onClick: ({ key }) => data.onMenu(key, n.id),
    }}>
      <div className={`cf-node ${selected ? "selected" : ""} ${data.invalid ? "error" : ""} ${data.flash ? "flash" : ""}`} data-testid={`node-${n.id}`}>
        {topIns.map((p, i) => (
          <Tooltip key={p.id} title={p.label}>
            <Handle type="target" id={p.id} position={Position.Top} className={p.kind === "PARAM" ? "param-handle" : "ref-handle"}
              style={{ left: 30 + i * 36, width: 12, height: 12 }} />
          </Tooltip>
        ))}
        {topIns.length > 0 && <div style={{ fontSize: 10, color: "#999", padding: "0 8px", height: 12 }}>{topIns.map((p) => p.label).join("  ")}</div>}
        <div className="title">
          <span>{NODE_META[n.type].icon}</span>
          <span>{n.label || NODE_META[n.type].label}</span>
          <span className="badges">
            {data.errors > 0 && <Tag color="red" style={{ marginRight: 2 }}>✖ {data.errors}</Tag>}
            {data.warns > 0 && <Tag color="orange" style={{ marginRight: 0 }}>⚠ {data.warns}</Tag>}
          </span>
        </div>
        <div className="ports">
          {Array.from({ length: rows }).map((_, i) => (
            <div key={i} className="port-row">
              {dataIns[i] && (
                <>
                  <Handle type="target" id={dataIns[i].id} position={Position.Left} style={{ top: "50%" }} />
                  <span className="port-label" title={dataIns[i].label}>{dataIns[i].label}</span>
                </>
              )}
              {mainOuts[i] && (
                <span style={{ position: "absolute", right: 0 }}>
                  <span className="port-label" title={mainOuts[i].label}>{mainOuts[i].label}</span>
                  {data.rows?.[mainOuts[i].id] !== undefined && <span className="cf-muted">（{data.rows[mainOuts[i].id]}行）</span>}
                  <Handle type="source" id={mainOuts[i].id} position={Position.Right} style={{ top: "50%" }} />
                </span>
              )}
            </div>
          ))}
        </div>
        {sideOuts.map((p, i) => (
          <Tooltip key={p.id} title={`${p.label}${data.rows?.[p.id] !== undefined ? `（${data.rows[p.id]} 行）` : ""}`}>
            <Handle type="source" id={p.id} position={Position.Bottom} className="side-handle" style={{ left: 30 + i * 30 }} />
          </Tooltip>
        ))}
      </div>
    </Dropdown>
  );
}

const nodeTypes = { cf: CfNode };

interface Props {
  pipeline: any;
  reload: () => void;
}

export default function Workspace(props: Props) {
  return (
    <ReactFlowProvider>
      <Inner {...props} />
    </ReactFlowProvider>
  );
}

function Inner({ pipeline, reload }: Props) {
  const { message, modal } = App.useApp();
  const rf = useReactFlow();
  const pid = pipeline.id;
  const [dsl, setDsl] = useState<Dsl>({ nodes: [], edges: [] });
  const [version, setVersion] = useState(0);
  const [baseInfo, setBaseInfo] = useState<any>(null);
  const [dirty, setDirty] = useState(false);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [undo, setUndo] = useState<Dsl[]>([]);
  const [redo, setRedo] = useState<Dsl[]>([]);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [layout, setLayout] = useState(loadLayout());
  const [sheetSource, setSheetSource] = useState<string | null>(null);
  const [sheet, setSheet] = useState<string | null>(null);
  const [fileInfo, setFileInfo] = useState<any>(null);
  const [selection, setSelection] = useState<RangeJson | null>(null);
  const [focusRange, setFocusRange] = useState<{ range: RangeJson; nonce: number } | null>(null);
  const [highlight, setHighlight] = useState<{ cell: string; nonce: number } | null>(null);
  const [job, setJob] = useState<any>(null);
  const [jobSig, setJobSig] = useState("");
  const [configWidth, setConfigWidth] = useState<number>(() => {
    try { return Number(localStorage.getItem(CONFIG_WIDTH_KEY)) || CONFIG_WIDTH; } catch { return CONFIG_WIDTH; }
  });
  const [measured, setMeasured] = useState<Record<string, { width: number; height: number }>>({});
  const [focusData, setFocusData] = useState<{ node: string; nonce: number } | null>(null);
  const [issues, setIssues] = useState<any[]>([]);
  const [regionPreview, setRegionPreview] = useState<any>(null);
  const [collapsed, setCollapsed] = useState(true);
  const [flash, setFlash] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [recent, setRecent] = useState<any[]>([]);
  const wrapper = useRef<HTMLDivElement>(null);
  const dslRef = useRef(dsl);
  dslRef.current = dsl;

  // ---------- 读取草稿 ----------
  const loadDraft = useCallback(async () => {
    const d = await get(`/api/pipelines/${pid}/draft`);
    setDsl({ ...d.dsl, nodes: d.dsl.nodes || [], edges: d.dsl.edges || [] });
    setVersion(d.draftVersion);
    setBaseInfo(d);
    setDirty(false);
    setUndo([]);
    setRedo([]);
  }, [pid]);
  useEffect(() => { loadDraft(); }, [loadDraft]);
  useEffect(() => {
    if (pipeline.sampleFileId) get(`/api/files/${pipeline.sampleFileId}`).then(setFileInfo).catch(() => setFileInfo(null));
    else setFileInfo(null);
  }, [pipeline.sampleFileId]);

  // ---------- 修改 / 撤销 ----------
  const change = useCallback((next: Dsl, record = true) => {
    if (record) {
      setUndo((u) => [...u.slice(-49), dslRef.current]);
      setRedo([]);
    }
    setDsl(next);
    setDirty(true);
  }, []);
  const doUndo = () => { if (!undo.length) return; setRedo((r) => [...r, dsl]); setDsl(undo[undo.length - 1]); setUndo(undo.slice(0, -1)); setDirty(true); };
  const doRedo = () => { if (!redo.length) return; setUndo((u) => [...u, dsl]); setDsl(redo[redo.length - 1]); setRedo(redo.slice(0, -1)); setDirty(true); };

  // ---------- 静态校验（防抖） ----------
  useEffect(() => {
    const t = setTimeout(() => post<Analysis>(`/api/pipelines/${pid}/schema`, { dsl }).then(setAnalysis).catch(() => {}), 500);
    return () => clearTimeout(t);
  }, [dsl, pid]);

  // ---------- 保存 ----------
  const save = useCallback(async (overwrite = false) => {
    try {
      let v = version;
      if (overwrite) v = (await get(`/api/pipelines/${pid}/draft`)).draftVersion;
      const r = await put(`/api/pipelines/${pid}/draft`, { dsl: dslRef.current, draftVersion: v });
      setVersion(r.draftVersion);
      setAnalysis(r.analysis);
      setDirty(false);
      setSavedAt(new Date().toLocaleTimeString("zh-CN", { hour12: false }));
      reload();
      return true;
    } catch (e) {
      if (e instanceof ApiError && e.code === "DSL_REV_CONFLICT") {
        modal.confirm({
          title: `草稿已被 ${e.data?.updatedBy} 于 ${e.data?.updatedAt?.replace("T", " ").slice(0, 19)} 更新`,
          content: "可以查看对方版本（放弃我的修改），或以我的为准覆盖。",
          okText: "以我的为准覆盖",
          cancelText: "查看对方版本",
          onOk: () => save(true),
          onCancel: () => loadDraft(),
        });
      } else message.error((e as Error).message);
      return false;
    }
  }, [version, pid, reload, loadDraft, message, modal]);

  // ---------- 离开保护、快捷键 ----------
  useEffect(() => {
    setLeaveGuard(() => !dirty || window.confirm("有未保存的修改，确定离开吗？"));
    const bu = (e: BeforeUnloadEvent) => { if (dirty) { e.preventDefault(); e.returnValue = ""; } };
    window.addEventListener("beforeunload", bu);
    return () => { setLeaveGuard(null); window.removeEventListener("beforeunload", bu); };
  }, [dirty]);
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key === "s") { e.preventDefault(); save(); }
      else if (mod && e.key === "z" && !e.shiftKey && !(e.target as HTMLElement).closest(".cm-editor,input,textarea")) { e.preventDefault(); doUndo(); }
      else if (mod && (e.key === "y" || (e.key === "z" && e.shiftKey)) && !(e.target as HTMLElement).closest(".cm-editor,input,textarea")) { e.preventDefault(); doRedo(); }
      else if (e.key === "Escape" && layout.mode !== "canvas") setMode("canvas");
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  });

  const setMode = (mode: Layout) => {
    const next = { ...layout, mode };
    setLayout(next);
    try { localStorage.setItem(LAYOUT_KEY, JSON.stringify({ ratio: next.ratio, lastMode: mode })); } catch { /* 忽略 */ }
  };

  // ---------- 画布数据 ----------
  const issueCount = useMemo(() => {
    const m: Record<string, { e: number; w: number }> = {};
    for (const i of issues) {
      const k = (m[i.node] ||= { e: 0, w: 0 });
      if (i.severity === "ERROR") k.e++;
      else if (i.severity === "WARN") k.w++;
    }
    return m;
  }, [issues]);
  const invalid = useMemo(() => new Set((analysis?.errors || []).map((e) => e.node).filter(Boolean)), [analysis]);

  const onMenu = useCallback((key: string, id: string) => {
    const n = dslRef.current.nodes.find((x) => x.id === id);
    if (!n) return;
    if (key === "delete") change({ nodes: dslRef.current.nodes.filter((x) => x.id !== id), edges: dslRef.current.edges.filter((e) => e.source.nodeId !== id && e.target.nodeId !== id) });
    if (key === "copy") {
      const nid = newId(n.type.toLowerCase(), dslRef.current.nodes.map((x) => x.id));
      change({ ...dslRef.current, nodes: [...dslRef.current.nodes, { ...structuredClone(n), id: nid, position: { x: (n.position?.x || 0) + 40, y: (n.position?.y || 0) + 40 } }] });
    }
    if (key === "output") { setSelected(id); setCollapsed(false); }
    if (key === "until") runTest(true, undefined, id);
  }, []);

  const rfNodes: Node<NodeData>[] = dsl.nodes.map((n, i) => ({
    id: n.id,
    type: "cf",
    measured: measured[n.id],
    position: n.position || { x: 80 + (i % 4) * 260, y: 60 + Math.floor(i / 4) * 200 },
    selected: n.id === selected,
    data: { node: n, errors: issueCount[n.id]?.e || 0, warns: issueCount[n.id]?.w || 0, rows: job?.metrics?.nodes?.[n.id]?.rows, invalid: invalid.has(n.id), flash: flash === n.id, onMenu },
  }));
  const rfEdges: Edge[] = dsl.edges.map((e) => {
    const t = dsl.nodes.find((n) => n.id === e.target.nodeId);
    const kind = t ? inputPorts(t).find((p) => p.id === e.target.portId)?.kind : "DATA";
    const s = dsl.nodes.find((n) => n.id === e.source.nodeId);
    const side = s ? outputPorts(s).find((p) => p.id === e.source.portId)?.side : false;
    const rejected = job?.metrics?.nodes?.[e.source.nodeId]?.rows?.[e.source.portId.replace("out_pass", "out_reject").replace("out_main", "out_unmatched").replace(/^out$/, "out_reject")] > 0;
    return {
      id: e.id, source: e.source.nodeId, sourceHandle: e.source.portId, target: e.target.nodeId, targetHandle: e.target.portId,
      style: { stroke: side || rejected ? "#fa8c16" : "#999", strokeDasharray: kind !== "DATA" ? "5 4" : undefined },
    };
  });

  const singleRow = (n: string, p: string) => !!analysis?.ports?.[n]?.[p]?.singleRow;
  const isValid = (c: Connection | Edge) => {
    const why = canConnect(dsl, c.source!, c.sourceHandle!, c.target!, c.targetHandle!, singleRow);
    return why === null;
  };

  const onConnect = (c: Connection) => {
    const why = canConnect(dsl, c.source, c.sourceHandle!, c.target, c.targetHandle!, singleRow);
    if (why) { message.warning(why); return; }
    const id = newId("e", dsl.edges.map((e) => e.id));
    const added = addEdge({ ...c, id }, []);
    void added;
    change({ ...dsl, edges: [...dsl.edges, { id, source: { nodeId: c.source, portId: c.sourceHandle! }, target: { nodeId: c.target, portId: c.targetHandle! } }] });
  };

  const addNode = (t: NodeType, pos?: { x: number; y: number }) => {
    const cur = dslRef.current;
    const id = newId(t === "EXCEL_SOURCE" ? "src" : t.toLowerCase(), cur.nodes.map((n) => n.id));
    const cfg = defaultConfig(t);
    if (t === "EXCEL_SOURCE" && fileInfo?.sheets?.[0]) cfg.sheet.value = fileInfo.sheets[0].name;
    if (t === "SINK") cfg.dataset = id.replace(/[^A-Za-z0-9_]/g, "_");
    // 点击添加时放在已有节点右侧，避免重叠
    const at = pos || { x: 80 + Math.max(0, ...cur.nodes.map((n) => (n.position?.x || 0))) + (cur.nodes.length ? 280 : 0), y: 80 };
    change({ ...cur, nodes: [...cur.nodes, { id, type: t, label: NODE_META[t].label, position: at, config: cfg }] });
    setSelected(id);
    if (!pos) setTimeout(() => rf.fitView({ maxZoom: 1, duration: 300 }), 60); // 点击添加的节点可能在视野外
    return id;
  };

  const onDrop = (ev: React.DragEvent) => {
    ev.preventDefault();
    const t = ev.dataTransfer.getData("application/cellflow") as NodeType;
    if (!t) return;
    addNode(t, rf.screenToFlowPosition({ x: ev.clientX, y: ev.clientY }));
  };

  // ---------- 试跑 ----------
  const runTest = async (preview: boolean, fileId?: number, until?: string) => {
    setRunning(true);
    try {
      const sig = dslSig(dslRef.current);
      const j = await post("/api/jobs/test", { pipelineId: pid, preview, fileId, untilNodeId: until, dsl: dslRef.current });
      setJob(j);
      setJobSig(sig);
      const iss = await get(`/api/jobs/${j.jobId}/issues?limit=500`);
      setIssues(iss.rows);
      setCollapsed(false);
      if (iss.rows.some((i: any) => i.severity === "ERROR")) message.warning(`试跑发现 ${iss.rows.filter((i: any) => i.severity === "ERROR").length} 个错误`);
      else message.success("试跑完成");
    } catch (e: any) {
      message.error(e.message);
    } finally {
      setRunning(false);
    }
  };

  // 表达式试算用的样例行：取最近一次试跑该端口的前 5 行（按需加载并缓存）
  const [samples, setSamples] = useState<Record<string, any[]>>({});
  const samplesReq = useRef(new Set<string>());
  useEffect(() => { setSamples({}); samplesReq.current.clear(); }, [job?.jobId]);
  const sampleRowsOf = (nodeId: string, portId: string) => {
    const k = `${nodeId}|${portId}`;
    if (!job) return [];
    if (!(k in samples) && !samplesReq.current.has(k)) {
      samplesReq.current.add(k);
      get(`/api/jobs/${job.jobId}/nodes/${nodeId}/ports/${portId}/rows?offset=0&limit=5`)
        .then((pg) => setSamples((m) => ({ ...m, [k]: pg.rows.map((r: any) => r.data) })))
        .catch(() => setSamples((m) => ({ ...m, [k]: [] })));
    }
    return samples[k] || [];
  };

  // ---------- 分屏与定位 ----------
  const sources = dsl.nodes.filter((n) => n.type === "EXCEL_SOURCE");
  const src = dsl.nodes.find((n) => n.id === sheetSource) || sources[0] || null;
  const sheets = (fileInfo?.sheets || []).map((s: any) => s.name);
  useEffect(() => {
    if (src && src.config.sheet?.match === "EXACT" && src.config.sheet.value) setSheet(src.config.sheet.value);
    else if (!sheet && sheets[0]) setSheet(sheets[0]);
  }, [src?.id, src?.config.sheet?.value, sheets.length]);

  const openSplit = (nodeId?: string) => {
    if (nodeId) { setSheetSource(nodeId); setSelected(nodeId); }
    if (layout.mode === "canvas") setMode(localStorage.getItem(LAYOUT_KEY)?.includes('"lastMode":"tb"') ? "tb" : "lr");
  };

  const locate = (addr: string, nodeId?: string) => {
    const [sh, cell] = addr.includes("!") ? [addr.slice(0, addr.lastIndexOf("!")), addr.slice(addr.lastIndexOf("!") + 1)] : [sheet, addr];
    const s = sources.find((n) => n.config.sheet?.value === sh) || src;
    if (s) setSheetSource(s.id);
    if (sh) setSheet(sh);
    setHighlight({ cell: cell!, nonce: Date.now() });
    openSplit();
    if (nodeId) { setFlash(nodeId); setTimeout(() => setFlash(null), 2200); }
  };

  const overlays: Overlay[] = useMemo(() => {
    if (!src || !sheet) return [];
    const report = (job?.metrics?.locateReport || []).filter((r: any) => r.node === src.id && r.sheet === sheet);
    return (src.config.regions || []).flatMap((r: any, i: number) => {
      const rep = report.find((x: any) => x.regionId === r.regionId);
      const range = rep?.range || r.designRange;
      return range ? [{ id: r.regionId, name: r.name, range, color: REGION_COLORS[i % REGION_COLORS.length], ignore: r.shape === "IGNORE" }] : [];
    });
  }, [src, sheet, job]);

  const selNode = dsl.nodes.find((n) => n.id === selected) || null;
  const updateNode = (id: string, patch: Partial<DslNode>) => {
    const nodes = dsl.nodes.map((n) => (n.id === id ? { ...n, ...patch } : n));
    // 区域删除后清理悬空连线
    const valid = new Set(nodes.flatMap((n) => outputPorts(n).map((p) => `${n.id}|${p.id}`)));
    const vin = new Set(nodes.flatMap((n) => inputPorts(n).map((p) => `${n.id}|${p.id}`)));
    change({ ...dsl, nodes, edges: dsl.edges.filter((e) => valid.has(`${e.source.nodeId}|${e.source.portId}`) && vin.has(`${e.target.nodeId}|${e.target.portId}`)) });
  };

  const changeSample = async (file: File) => {
    try {
      const f = await upload(file);
      const r = await put(`/api/pipelines/${pid}/sample-file`, { fileId: f.fileId });
      reload();
      if (r.locateReport?.length) {
        modal.info({ title: "已更换样例文件，各区域定位变化", width: 640, content: (
          <ul>{r.locateReport.map((x: any) => <li key={x.node + x.regionId}>{x.name}：{x.designRange?.a1} → {x.range.a1}</li>)}</ul>
        ) });
      } else message.success("样例文件已更新");
    } catch (e: any) {
      message.error(e.message);
    }
  };

  const errCount = analysis?.errors?.length || 0;
  const sheetPane = (
    <SheetPane fileId={pipeline.sampleFileId} sheets={sheets} sheet={sheet} onSheet={setSheet} overlays={overlays} highlight={highlight}
      focusRange={focusRange} onSelection={setSelection}
      toolbar={<Space size={4}><span className="cf-muted">样例文件</span><Tag>{fileInfo?.fileName || "未上传"}</Tag><span className="cf-muted">源节点</span>
        <Select size="small" style={{ width: 140 }} value={src?.id} onChange={(v) => { setSheetSource(v); setSelected(v); }} options={sources.map((s) => ({ value: s.id, label: s.label || s.id }))} /></Space>} />
  );

  const canvas = (
    <div className="cf-canvas" ref={wrapper} onDragOver={(e) => e.preventDefault()} onDrop={onDrop}>
      <ReactFlow nodes={rfNodes} edges={rfEdges} nodeTypes={nodeTypes} fitView fitViewOptions={{ maxZoom: 1 }} minZoom={0.2}
        onNodesChange={(chs) => {
          // React Flow 12 受控模式：必须回填测得的尺寸，否则节点一直是 visibility: hidden
          const dims = chs.filter((c: any) => c.type === "dimensions" && c.dimensions);
          if (dims.length) setMeasured((m) => ({ ...m, ...Object.fromEntries(dims.map((c: any) => [c.id, c.dimensions])) }));
          const moves = chs.filter((c: any) => c.type === "position" && c.position);
          if (moves.length) change({ ...dsl, nodes: dsl.nodes.map((n) => { const m: any = moves.find((c: any) => c.id === n.id); return m ? { ...n, position: m.position } : n; }) }, false);
          const sel = chs.find((c: any) => c.type === "select" && c.selected) as any;
          if (sel) setSelected(sel.id);
        }}
        onNodesDelete={(ns) => { const ids = new Set(ns.map((n) => n.id)); change({ nodes: dsl.nodes.filter((n) => !ids.has(n.id)), edges: dsl.edges.filter((e) => !ids.has(e.source.nodeId) && !ids.has(e.target.nodeId)) }); }}
        onEdgesDelete={(es) => { const ids = new Set(es.map((e) => e.id)); change({ ...dsl, edges: dsl.edges.filter((e) => !ids.has(e.id)) }); }}
        onConnect={onConnect} isValidConnection={isValid}
        onNodeDoubleClick={(_, n) => { const d = dsl.nodes.find((x) => x.id === n.id); if (d?.type === "EXCEL_SOURCE") openSplit(n.id); }}
        onPaneClick={() => setSelected(null)}
        deleteKeyCode={["Delete", "Backspace"]} panOnScroll selectionOnDrag={false} panActivationKeyCode="Space">
        <Background />
        <Controls />
        {layout.mode === "canvas" && <MiniMap pannable zoomable />}
      </ReactFlow>
    </div>
  );

  const palette = (
    <div className={`cf-palette ${layout.mode !== "canvas" ? "collapsed" : ""}`}>
      {(["源", "变换", "关联", "校验", "输出"] as const).map((g) => (
        <div key={g}>
          {layout.mode === "canvas" && <div className="cf-muted" style={{ margin: "6px 0 4px" }}>{g}</div>}
          {(Object.keys(NODE_META) as NodeType[]).filter((t) => NODE_META[t].group === g).map((t) => (
            <Tooltip key={t} title={<div><b>{NODE_META[t].label}</b><div>{NODE_META[t].desc}</div><div style={{ opacity: 0.7 }}>点击添加，或拖到画布指定位置</div></div>} placement="right">
              <div className="item" draggable data-testid={`palette-${t}`} onDragStart={(e) => e.dataTransfer.setData("application/cellflow", t)}
                onClick={() => addNode(t)}>
                {NODE_META[t].icon} {layout.mode === "canvas" ? NODE_META[t].label : ""}
              </div>
            </Tooltip>
          ))}
        </div>
      ))}
    </div>
  );

  const config = (
    <div className="cf-config" data-testid="config-panel" style={{ width: configWidth }}>
      {selNode?.type === "EXCEL_SOURCE" ? (
        <RegionPanel node={selNode} fileId={pipeline.sampleFileId} sheets={sheets} selection={selection} issues={issues}
          onChange={(cfg) => updateNode(selNode.id, { config: cfg })} onFocus={(r) => { openSplit(selNode.id); setFocusRange({ range: r, nonce: Date.now() }); }}
          onPreview={async (region) => {
            try {
              const sh = selNode.config.sheet?.match === "EXACT" ? selNode.config.sheet.value : sheet;
              setRegionPreview(await post("/api/regions/preview", { fileId: pipeline.sampleFileId, sheet: sh, region: { ...region, outputPortId: region.outputPortId } }));
              setCollapsed(false);
            } catch (e: any) { message.error(e.message); }
          }} />
      ) : selNode ? (
        <NodeConfig node={selNode} dsl={dsl} analysis={analysis} pipeline={pipeline} sampleRows={sampleRowsOf}
          job={job} stale={!!job && jobSig !== dslSig(dsl)} running={running} onRunUntil={(id) => runTest(true, undefined, id)}
          onShowAll={(id) => { setCollapsed(false); setFocusData({ node: id, nonce: Date.now() }); }}
          onChange={(cfg) => updateNode(selNode.id, { config: cfg })} onLabel={(l) => updateNode(selNode.id, { label: l })} />
      ) : (
        <div>
          <h4>方案概览</h4>
          <p>节点 {dsl.nodes.length} 个；输出表：{dsl.nodes.filter((n) => n.type === "SINK").map((n) => n.config.binding?.table || "未绑定").join("、") || "无"}</p>
          {errCount > 0 && <div className="cf-err">静态校验：{errCount} 个错误</div>}
          {(analysis?.errors || []).slice(0, 10).map((e, i) => <div key={i} className="cf-err" style={{ fontSize: 12 }}>· {e.message}</div>)}
          {job && <p>最近试跑：任务 #{job.jobId}，错误 {job.issues.error || 0}，警告 {job.issues.warn || 0}</p>}
          <GettingStarted dsl={dsl} fileName={fileInfo?.fileName} job={job} fresh={!!job && jobSig === dslSig(dsl)} published={!!pipeline.publishedRev}
            onAddSource={() => { const s0 = dsl.nodes.find((n) => n.type === "EXCEL_SOURCE"); openSplit(s0 ? s0.id : addNode("EXCEL_SOURCE")); }}
            onAddSink={() => addNode("SINK")} onPreview={() => runTest(true)} onSelect={(id: string) => setSelected(id)} />
        </div>
      )}
    </div>
  );

  // 右侧配置面板宽度可拖动调整（记住在本机）；双击分隔条恢复默认
  const configHandle = (
    <div className="cf-splitter" id="config-splitter" title="拖动调整右侧面板宽度，双击恢复默认"
      onDoubleClick={() => { setConfigWidth(CONFIG_WIDTH); try { localStorage.removeItem(CONFIG_WIDTH_KEY); } catch { /* 忽略 */ } }}
      onMouseDown={(e) => {
        e.preventDefault();
        const start = e.clientX; const w0 = configWidth;
        let w = w0;
        document.body.style.userSelect = "none";
        const move = (ev: MouseEvent) => { w = Math.round(Math.min(window.innerWidth * 0.7, Math.max(320, w0 - (ev.clientX - start)))); setConfigWidth(w); };
        const up = () => {
          document.body.style.userSelect = "";
          window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up);
          try { localStorage.setItem(CONFIG_WIDTH_KEY, String(w)); } catch { /* 忽略 */ }
        };
        window.addEventListener("mousemove", move); window.addEventListener("mouseup", up);
      }} />
  );

  const body = layout.mode === "canvas" ? (
    <div className="cf-ws-body">{palette}{canvas}{configHandle}{config}</div>
  ) : layout.mode === "lr" ? (
    <div className="cf-ws-body">
      {palette}
      <div style={{ flex: layout.ratio, display: "flex", minWidth: 300 }}>{sheetPane}</div>
      <div className="cf-splitter" onMouseDown={(e) => {
        const start = e.clientX; const w = wrapper.current?.parentElement?.clientWidth || 1200; const r0 = layout.ratio;
        const move = (ev: MouseEvent) => setLayout((l) => ({ ...l, ratio: Math.min(0.8, Math.max(0.3, r0 + (ev.clientX - start) / w)) }));
        const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); };
        window.addEventListener("mousemove", move); window.addEventListener("mouseup", up);
      }} />
      <div style={{ flex: 1 - layout.ratio, display: "flex", minWidth: 200 }}>{canvas}</div>
      {configHandle}
      {config}
    </div>
  ) : (
    <div className="cf-ws-body">
      {palette}
      <div style={{ flex: 1, display: "flex", flexDirection: "column" }}>
        <div style={{ flex: layout.ratio, display: "flex", minHeight: 200 }}>{sheetPane}</div>
        <div style={{ flex: 1 - layout.ratio, display: "flex", borderTop: "1px solid #eee" }}>{canvas}</div>
      </div>
      {configHandle}
      {config}
    </div>
  );

  return (
    <div className="cf-workspace">
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 12px", borderBottom: "1px solid #eee", flexWrap: "wrap" }}>
        <span className="cf-muted">样例文件</span>
        <Tag id="sample-file-name">{fileInfo?.fileName || "未上传"}</Tag>
        <Upload accept=".xlsx,.xlsm" showUploadList={false} customRequest={({ file }) => changeSample(file as File)}>
          <Button size="small" id="change-sample">{fileInfo ? "更换" : "上传"}</Button>
        </Upload>
        <span className="cf-muted" id="draft-status">
          {dirty ? <span className="cf-warn">● 有未保存修改</span> : savedAt ? `已保存 ${savedAt}` : baseInfo?.baseRev ? `基于 v${baseInfo.baseRev}` : "草稿"}
        </span>
        <span style={{ marginLeft: "auto" }} />
        <Radio.Group size="small" value={layout.mode} onChange={(e) => setMode(e.target.value)} optionType="button"
          options={[{ value: "canvas", label: "仅画布" }, { value: "lr", label: "左右分屏" }, { value: "tb", label: "上下分屏" }]} />
        <Button size="small" onClick={doUndo} disabled={!undo.length}>撤销</Button>
        <Button size="small" onClick={doRedo} disabled={!redo.length}>重做</Button>
        <Button size="small" id="save-draft" type={dirty ? "primary" : "default"} onClick={() => save()}>保存草稿</Button>
        <Button size="small" id="run-preview" loading={running} onClick={() => runTest(true)}>预览</Button>
        <Dropdown menu={{
          items: [
            { key: "sample", label: "用样例文件" },
            { key: "upload", label: <Upload accept=".xlsx" showUploadList={false} customRequest={async ({ file }) => { const f = await upload(file as File); runTest(false, f.fileId); }}>重新上传一个文件</Upload> },
            { key: "recent", label: "选最近某个任务的真实文件", children: recent.length ? recent.map((r) => ({ key: `r${r.fileId}`, label: `${r.fileName}（${r.client || "控制台"}）` })) : [{ key: "none", label: "暂无", disabled: true }] },
          ],
          onClick: ({ key }) => { if (key === "sample") runTest(false); else if (key.startsWith("r")) runTest(false, Number(key.slice(1))); },
        }} onOpenChange={(o) => { if (o) get(`/api/pipelines/${pid}/recent-files`).then(setRecent).catch(() => {}); }}>
          <Button size="small" id="run-full" loading={running}>完整试跑 ▾</Button>
        </Dropdown>
        <Tooltip title={errCount ? `存在 ${errCount} 个静态校验错误，不能发布` : ""}>
          <Button size="small" id="goto-publish" type="primary" disabled={errCount > 0} onClick={async () => { if (dirty && !(await save())) return; navigate(`/pipelines/${pid}/publish`); }}>发布…</Button>
        </Tooltip>
      </div>
      {body}
      <ResultPanel job={job} issues={issues} dsl={dsl} selectedNode={selected} focusData={focusData} regionPreview={regionPreview} collapsed={collapsed}
        onToggle={() => setCollapsed(!collapsed)} onIssue={(i) => { if (i.cell) locate(`${i.sheet}!${i.cell}`, i.node); else { setFlash(i.node); setTimeout(() => setFlash(null), 2200); } }}
        onCell={(a) => locate(a)} />
    </div>
  );
}
