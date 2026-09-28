import { autocompletion, CompletionContext } from "@codemirror/autocomplete";
import { EditorState } from "@codemirror/state";
import { EditorView, keymap, placeholder as ph } from "@codemirror/view";
import { Button, Input, Popover, Space, Typography } from "antd";
import { minimalSetup } from "codemirror";
import { useEffect, useRef, useState } from "react";
import { post } from "../../api";

export interface ExprContext {
  fields: Record<string, string>; // 字段 → 类型
  params: Record<string, Record<string, string>>; // 别名 → 字段 → 类型
  sampleRows?: any[];
  paramValues?: Record<string, any>; // 别名 → 参数行（单行）
  expect?: "bool";
}

let catalog: { name: string; doc: string }[] | null = null;

// 语法速查（示例均已在后端表达式引擎中验证可编译）；点击示例插入到当前编辑器
const HELP: { title: string; items: [string, string][] }[] = [
  { title: "基础", items: [
    ["baseHp * 2", "字段直接写名字；+ - * / % 四则运算"],
    ["double(baseHp) * 1.5", "整数和小数不能直接混算，先用 double() 转成小数"],
    ["count / 3", "整数相除只保留整数部分；要小数写 double(count) / 3.0"],
    ["'战士'", "文本用单引号或双引号"],
  ] },
  { title: "比较与条件", items: [
    ["level >= 3", "比较：== != > >= < <="],
    ["job == '战士' && level > 1", "并且 &&，或者 ||，取反 !"],
    ["job in ['战士', '法师']", "是否在列表中"],
    ["level >= 3 ? '高级' : '普通'", "条件 ? 成立时的值 : 不成立时的值"],
  ] },
  { title: "空值", items: [
    ["coalesce(count, 0)", "为空时用默认值（任一字段为空时整个表达式结果为空，除非用 coalesce / isNull）"],
    ["isNull(name)", "是否为空"],
  ] },
  { title: "文本", items: [
    ["job + '-' + string(level)", "拼接；数字先用 string() 转文本"],
    ["upper(trim(name))", "去首尾空白、转大写 / lower 转小写"],
    ["name.contains('碎片')", "包含；另有 startsWith / endsWith"],
    ["name.matches('^[A-Z]+$')", "正则匹配"],
    ["len(name) > 2", "文本长度"],
    ["substr(name, 0, 2)", "截取：从第 0 位开始取 2 个字"],
    ["replace(name, ' ', '')", "替换"],
    ["split(name, ',')", "按分隔符拆成列表；size(列表) 取个数"],
  ] },
  { title: "数字", items: [
    ["round(double(price) * 1.06, 2)", "四舍五入到 2 位；floor 向下 / ceil 向上取整"],
    ["abs(count - 10)", "绝对值；max(a, b) / min(a, b) / clamp(x, 最小, 最大)"],
    ["int('12')", "类型转换：int() 整数、double() 小数、string() 文本"],
  ] },
  { title: "日期", items: [
    ["formatDate(d, '%Y-%m-%d')", "日期转文本"],
    ["addDays(d, 7)", "加减天数"],
    ["diffDays(d, date('2026-01-01'))", "相差天数；date('…') 文本转日期"],
  ] },
  { title: "参数与行信息", items: [
    ["params.global.hpRate", "参数端口（键值区连到节点顶部虚线圆点）的字段：params.别名.字段"],
    ["meta.sheetRow", "该行在 Excel 里的行号；meta.index 为区域内序号"],
  ] },
];

/** 表达式编辑器（WIREFRAME P3-2 公共组件）：补全字段 / params. / meta. / 函数；实时检查；类型修复；快速预览。 */
// 最近获得焦点的表达式编辑器：字段面板点击字段时插入到这里
let activeView: EditorView | null = null;
let lastView: EditorView | null = null;

/** 把文本插入到最近使用的表达式编辑器光标处；没有可用的编辑器时返回 false。 */
export function insertIntoExpr(text: string): boolean {
  const v = activeView?.dom.isConnected ? activeView : lastView?.dom.isConnected ? lastView : null;
  if (!v) return false;
  const { from, to } = v.state.selection.main;
  v.dispatch({ changes: { from, to, insert: text }, selection: { anchor: from + text.length } });
  v.focus();
  return true;
}

export default function ExprEditor({ value, onChange, ctx, id }: { value: string; onChange: (v: string) => void; ctx: ExprContext; id?: string }) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const ctxRef = useRef(ctx);
  ctxRef.current = ctx;
  const [check, setCheck] = useState<any>(null);

  useEffect(() => {
    if (!host.current) return;
    const complete = (c: CompletionContext) => {
      const word = c.matchBefore(/[\w.]*/);
      if (!word || (word.from === word.to && !c.explicit)) return null;
      const text = word.text;
      const x = ctxRef.current;
      let options: any[] = [];
      if (text.startsWith("params.")) {
        const parts = text.split(".");
        if (parts.length === 2) options = Object.keys(x.params).map((a) => ({ label: `params.${a}`, type: "namespace" }));
        else options = Object.entries(x.params[parts[1]] || {}).map(([f, t]) => ({ label: `params.${parts[1]}.${f}`, detail: t, type: "property" }));
      } else if (text.startsWith("meta.")) {
        options = [{ label: "meta.index", detail: "区域内序号" }, { label: "meta.sheetRow", detail: "Excel 行号" }];
      } else {
        options = [
          ...Object.entries(x.fields).map(([f, t]) => ({ label: f, detail: t, type: "variable" })),
          { label: "params.", type: "namespace", detail: "参数端口" },
          { label: "meta.", type: "namespace", detail: "行信息" },
          ...(catalog || []).map((f) => ({ label: f.name, info: f.doc, type: "function", apply: f.name + "(" })),
        ];
      }
      return { from: word.from, options };
    };
    view.current = new EditorView({
      parent: host.current,
      state: EditorState.create({
        doc: value,
        extensions: [
          minimalSetup,
          autocompletion({ override: [complete] }),
          ph("例如：count > 0 && itemId != 0"),
          keymap.of([]),
          EditorView.lineWrapping,
          EditorView.domEventHandlers({ focus: (_e, v) => { activeView = v; } }),
          EditorView.updateListener.of((u) => { if (u.docChanged) onChange(u.state.doc.toString()); }),
          EditorView.theme({ "&": { border: "1px solid #d9d9d9", borderRadius: "4px", fontSize: "13px" }, ".cm-content": { fontFamily: "Menlo, Consolas, monospace", minHeight: "22px" } }),
        ],
      }),
    });
    lastView = view.current;
    fetch("/api/expressions/functions").then((r) => r.json()).then((b) => { catalog = b.data; }).catch(() => {});
    return () => {
      if (activeView === view.current) activeView = null;
      if (lastView === view.current) lastView = null;
      view.current?.destroy();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const v = view.current;
    if (v && v.state.doc.toString() !== value) v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: value } });
  }, [value]);

  useEffect(() => {
    if (!value.trim()) { setCheck(null); return; }
    const t = setTimeout(() => {
      post("/api/expressions/check", { expr: value, fields: ctx.fields, params: ctx.params, sampleRows: (ctx.sampleRows || []).slice(0, 5), paramValues: ctx.paramValues, expect: ctx.expect })
        .then(setCheck).catch(() => setCheck(null));
    }, 400);
    return () => clearTimeout(t);
  }, [value, JSON.stringify(ctx.fields), JSON.stringify(ctx.params), JSON.stringify((ctx.sampleRows || []).slice(0, 5)), JSON.stringify(ctx.paramValues), ctx.expect]);

  const [q, setQ] = useState("");
  const insertHere = (t: string) => {
    const v = view.current;
    if (!v) return;
    const { from, to } = v.state.selection.main;
    v.dispatch({ changes: { from, to, insert: t }, selection: { anchor: from + t.length } });
    v.focus();
  };
  const match = (a: string, b: string) => !q || a.toLowerCase().includes(q.toLowerCase()) || b.includes(q);
  const help = (
    <div style={{ width: 460, maxHeight: 460, overflow: "auto" }} className="expr-help">
      <Input size="small" allowClear placeholder="搜索，如：日期、空值、round" value={q} onChange={(e) => setQ(e.target.value)} style={{ marginBottom: 6 }} />
      <div className="cf-muted" style={{ marginBottom: 6 }}>点击示例插入到表达式；输入时也会自动提示字段和函数</div>
      {HELP.map((sec) => {
        const items = sec.items.filter(([a, b]) => match(a, b + sec.title));
        if (!items.length) return null;
        return (
          <div key={sec.title} style={{ marginBottom: 8 }}>
            <div style={{ fontWeight: 600 }}>{sec.title}</div>
            {items.map(([code, desc]) => (
              <div key={code} style={{ display: "flex", gap: 8, padding: "2px 0" }}>
                <a className="cf-mono" style={{ whiteSpace: "nowrap" }} onClick={() => insertHere(code)}>{code}</a>
                <span className="cf-muted">{desc}</span>
              </div>
            ))}
          </div>
        );
      })}
      {(catalog || []).filter((f) => match(f.name, f.doc)).length > 0 && (
        <div>
          <div style={{ fontWeight: 600 }}>全部函数</div>
          {(catalog || []).filter((f) => match(f.name, f.doc)).map((f) => (
            <div key={f.name} style={{ padding: "1px 0" }}>
              <a className="cf-mono" onClick={() => insertHere(f.name + "(")}>{f.name}</a> <span className="cf-muted">{f.doc}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );

  return (
    <div id={id}>
      <div ref={host} />
      <Popover content={help} title="表达式语法" trigger="click" placement="leftTop">
        <a style={{ fontSize: 12 }} className="expr-help-link">语法帮助</a>
      </Popover>
      {check && (
        <div style={{ fontSize: 12, marginTop: 4 }}>
          {check.ok ? (
            <Typography.Text type="success">✓ 结果类型：{check.resultType}</Typography.Text>
          ) : (
            <Space direction="vertical" size={2}>
              <Typography.Text type="danger" className="expr-error">✗ {check.message}</Typography.Text>
              {check.hint && <Typography.Text type="secondary">{check.hint}</Typography.Text>}
              {check.fix && <Button size="small" onClick={() => onChange(check.fix)}>插入类型转换：{check.fix}</Button>}
            </Space>
          )}
          {check.ok && check.preview?.length > 0 && (
            <div className="cf-muted">预览：{check.preview.map((p: any, i: number) => <span key={i} style={{ marginRight: 8 }}>{p.error ? `✗${p.error}` : JSON.stringify(p.value)}</span>)}</div>
          )}
        </div>
      )}
    </div>
  );
}
