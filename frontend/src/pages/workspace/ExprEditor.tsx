import { autocompletion, CompletionContext } from "@codemirror/autocomplete";
import { EditorState } from "@codemirror/state";
import { EditorView, keymap, placeholder as ph } from "@codemirror/view";
import { Button, Space, Typography } from "antd";
import { minimalSetup } from "codemirror";
import { useEffect, useRef, useState } from "react";
import { post } from "../../api";

export interface ExprContext {
  fields: Record<string, string>; // 字段 → 类型
  params: Record<string, Record<string, string>>; // 别名 → 字段 → 类型
  sampleRows?: any[];
  expect?: "bool";
}

let catalog: { name: string; doc: string }[] | null = null;

/** 表达式编辑器（WIREFRAME P3-2 公共组件）：补全字段 / params. / meta. / 函数；实时检查；类型修复；快速预览。 */
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
          EditorView.updateListener.of((u) => { if (u.docChanged) onChange(u.state.doc.toString()); }),
          EditorView.theme({ "&": { border: "1px solid #d9d9d9", borderRadius: "4px", fontSize: "13px" }, ".cm-content": { fontFamily: "Menlo, Consolas, monospace", minHeight: "22px" } }),
        ],
      }),
    });
    fetch("/api/expressions/functions").then((r) => r.json()).then((b) => { catalog = b.data; }).catch(() => {});
    return () => view.current?.destroy();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const v = view.current;
    if (v && v.state.doc.toString() !== value) v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: value } });
  }, [value]);

  useEffect(() => {
    if (!value.trim()) { setCheck(null); return; }
    const t = setTimeout(() => {
      post("/api/expressions/check", { expr: value, fields: ctx.fields, params: ctx.params, sampleRows: (ctx.sampleRows || []).slice(0, 5), expect: ctx.expect })
        .then(setCheck).catch(() => setCheck(null));
    }, 400);
    return () => clearTimeout(t);
  }, [value, JSON.stringify(ctx.fields), JSON.stringify(ctx.params), ctx.expect]);

  return (
    <div id={id}>
      <div ref={host} />
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
