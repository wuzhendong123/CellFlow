// 画布 DSL 类型与工具（与后端 cellflow/engine/nodes.py 的端口定义保持一致）

export type NodeType = "EXCEL_SOURCE" | "FILTER" | "DERIVE" | "SELECT_RENAME" | "UNION" | "LOOKUP" | "JOIN" | "VALIDATOR" | "SINK";

export interface DslNode {
  id: string;
  type: NodeType;
  label?: string;
  position?: { x: number; y: number };
  config: any;
}

export interface DslEdge {
  id: string;
  source: { nodeId: string; portId: string };
  target: { nodeId: string; portId: string };
}

export interface Dsl {
  dslVersion?: string;
  pipelineCode?: string;
  nodes: DslNode[];
  edges: DslEdge[];
}

export type PortKind = "DATA" | "PARAM" | "REF";
export interface PortSpec {
  id: string;
  label: string;
  kind: PortKind;
  side?: boolean;
  multi?: boolean;
  optional?: boolean;
}

export const NODE_META: Record<NodeType, { label: string; group: string; icon: string; desc: string }> = {
  EXCEL_SOURCE: { label: "Excel 源", group: "源", icon: "📄", desc: "读取一个 Sheet，圈选其中的表格区域；每个区域是一个输出端口。双击进入圈选，可「自动识别」。" },
  FILTER: { label: "过滤", group: "变换", icon: "⏷", desc: "只保留满足条件的行，例如 count > 0。" },
  DERIVE: { label: "派生列", group: "变换", icon: "ƒ", desc: "用表达式新增或覆盖列，例如 hp = int(double(baseHp) * 1.5)。" },
  SELECT_RENAME: { label: "选列改名", group: "变换", icon: "☰", desc: "挑选需要的列、改列名、调整顺序。" },
  UNION: { label: "合并", group: "变换", icon: "∪", desc: "把多条结构相近的数据上下拼接（按列名对齐）。" },
  LOOKUP: { label: "查表映射", group: "变换", icon: "⇄", desc: "用一张字典表把编码翻译成名称，例如道具 ID → 道具名。" },
  JOIN: { label: "关联", group: "关联", icon: "⋈", desc: "两份数据按键左右关联（类似 SQL JOIN），例如奖励明细关联道具表。" },
  VALIDATOR: { label: "校验", group: "校验", icon: "✓", desc: "按规则检查数据：必填、唯一、范围、引用存在、与汇总对账等；不通过的行进「被拒」端口。" },
  SINK: { label: "输出到业务表", group: "输出", icon: "⛁", desc: "把数据写入一张 MySQL 业务表：选表、映射字段、选主键。" },
};

export const SHAPES: { value: string; label: string }[] = [
  { value: "DETAIL", label: "明细" },
  { value: "KEY_VALUE", label: "键值" },
  { value: "MATRIX", label: "矩阵" },
  { value: "SUMMARY", label: "汇总" },
  { value: "IGNORE", label: "屏蔽" },
  { value: "GROUPED_DETAIL", label: "分组明细" },
  { value: "FORM", label: "表单型" },
  { value: "REPEATING_BLOCK", label: "重复块" },
];

export function inputPorts(n: DslNode): PortSpec[] {
  switch (n.type) {
    case "EXCEL_SOURCE":
      return [];
    case "FILTER":
    case "DERIVE":
      return [{ id: "in", label: "输入", kind: "DATA" }, { id: "in_params", label: "参数", kind: "PARAM", optional: true }];
    case "SELECT_RENAME":
    case "SINK":
      return [{ id: "in", label: "输入", kind: "DATA" }];
    case "UNION":
      return [{ id: "in", label: "输入（可多条）", kind: "DATA", multi: true }];
    case "LOOKUP":
      return [{ id: "in", label: "主数据", kind: "DATA" }, { id: "in_dict", label: "字典", kind: "DATA" }];
    case "JOIN":
      return [{ id: "in_left", label: "左", kind: "DATA" }, { id: "in_right", label: "右", kind: "DATA" }];
    case "VALIDATOR": {
      const refs = Object.entries(n.config?.refs || {}).map(([alias, port]) => ({ id: String(port), label: `引用 ${alias}`, kind: "REF" as PortKind }));
      return [{ id: "in_main", label: "主数据", kind: "DATA" }, { id: "in_params", label: "参数", kind: "PARAM", optional: true }, ...refs];
    }
  }
}

export function outputPorts(n: DslNode): PortSpec[] {
  switch (n.type) {
    case "EXCEL_SOURCE":
      return (n.config?.regions || [])
        .filter((r: any) => r.shape !== "IGNORE")
        .map((r: any) => ({ id: r.outputPortId, label: r.name || r.outputPortId, kind: "DATA" as PortKind }));
    case "DERIVE":
      return [{ id: "out", label: "输出", kind: "DATA" }, { id: "out_reject", label: "被拒行", kind: "DATA", side: true }];
    case "FILTER":
    case "SELECT_RENAME":
    case "UNION":
    case "LOOKUP":
      return [{ id: "out", label: "输出", kind: "DATA" }];
    case "JOIN":
      return [{ id: "out_main", label: "输出", kind: "DATA" }, { id: "out_unmatched", label: "未匹配", kind: "DATA", side: true }];
    case "VALIDATOR":
      return [{ id: "out_pass", label: "通过", kind: "DATA" }, { id: "out_reject", label: "被拒行", kind: "DATA", side: true }];
    case "SINK":
      return [];
  }
}

export function defaultConfig(t: NodeType): any {
  switch (t) {
    case "EXCEL_SOURCE":
      return { sheet: { match: "EXACT", value: "" }, loaderOptions: { mergePolicy: "FILL", hiddenRows: "KEEP_WARN", strikethroughRows: "KEEP" }, regions: [] };
    case "FILTER":
      return { expr: "", params: {} };
    case "DERIVE":
      return { columns: [], params: {}, onError: "ERROR" };
    case "SELECT_RENAME":
      return { columns: [] };
    case "UNION":
      return {};
    case "LOOKUP":
      return { on: [], select: [], onMissing: "NULL" };
    case "JOIN":
      return { joinType: "LEFT", leftAlias: "left", rightAlias: "right", on: [], expectedCardinality: "MANY_TO_ONE", conflictPolicy: { mode: "EXPLICIT_THEN_PREFIX", aliases: {}, keyColumns: "MERGE" }, select: ["left.*", "right.*"], unmatchedPolicy: "SIDE_OUTPUT" };
    case "VALIDATOR":
      return { rules: [], params: {}, refs: {} };
    case "SINK":
      return { dataset: "", binding: { table: "", strategy: "SWAP", keyFields: null, columnMapping: [], guards: null } };
  }
}

export function newId(prefix: string, existing: string[]): string {
  let i = 1;
  while (existing.includes(`${prefix}_${i}`)) i++;
  return `${prefix}_${i}`;
}

export function colLetter(c: number): string {
  let s = "";
  while (c > 0) {
    const m = (c - 1) % 26;
    s = String.fromCharCode(65 + m) + s;
    c = Math.floor((c - 1) / 26);
  }
  return s;
}

export function colNumber(s: string): number {
  let n = 0;
  for (const ch of s.toUpperCase()) n = n * 26 + (ch.charCodeAt(0) - 64);
  return n;
}

export interface RangeJson {
  startRow: number;
  startCol: number;
  endRow: number;
  endCol: number;
  a1?: string;
}

export function toA1(r: RangeJson): string {
  return `${colLetter(r.startCol)}${r.startRow}:${colLetter(r.endCol)}${r.endRow}`;
}

export function parseA1(s: string): RangeJson | null {
  const m = s.trim().toUpperCase().match(/^([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?$/);
  if (!m) return null;
  const r1 = Number(m[2]);
  const c1 = colNumber(m[1]);
  const r2 = m[4] ? Number(m[4]) : r1;
  const c2 = m[3] ? colNumber(m[3]) : c1;
  const out = { startRow: Math.min(r1, r2), startCol: Math.min(c1, c2), endRow: Math.max(r1, r2), endCol: Math.max(c1, c2) };
  return { ...out, a1: toA1(out) };
}

export const REGION_COLORS = ["#e6f4ff", "#f6ffed", "#fff7e6", "#f9f0ff", "#e6fffb", "#fff0f6", "#fcffe6", "#f0f5ff"];

/** 连线合法性（WIREFRAME P3 画布交互）：源节点无输入、参数端口只接单行流、无环、单入边端口只接一条。 */
export function canConnect(dsl: Dsl, src: string, srcPort: string, tgt: string, tgtPort: string, singleRow: (n: string, p: string) => boolean): string | null {
  const t = dsl.nodes.find((n) => n.id === tgt);
  if (!t) return "目标节点不存在";
  if (t.type === "EXCEL_SOURCE") return "源节点没有输入端口";
  if (src === tgt) return "不能连到自身";
  const spec = inputPorts(t).find((p) => p.id === tgtPort);
  if (!spec) return "端口不存在";
  if (spec.kind === "PARAM" && !singleRow(src, srcPort)) return "参数端口只接受单行数据（如键值区、汇总区）";
  if (!spec.multi && dsl.edges.some((e) => e.target.nodeId === tgt && e.target.portId === tgtPort)) return "该端口只能接一条线";
  // 环检测：从 tgt 出发能否到达 src
  const stack = [tgt];
  const seen = new Set<string>();
  while (stack.length) {
    const n = stack.pop()!;
    if (n === src) return "连线会形成环";
    if (seen.has(n)) continue;
    seen.add(n);
    dsl.edges.filter((e) => e.source.nodeId === n).forEach((e) => stack.push(e.target.nodeId));
  }
  return null;
}
