import { App, Button, Col, Form, Input, InputNumber, Modal, Row, Select, Space, Switch, Table, Tag, Typography } from "antd";
import { useEffect, useState } from "react";
import { get, opCall } from "../../api";

const SAMPLE = `# 签名：HMAC-SHA256(secret, method + "\\n" + path + "\\n" + timestamp + "\\n" + nonce + "\\n" + sha256(body))
POST /open/v1/jobs            (multipart: file + meta)
  X-CF-AppKey: <AppKey>
  X-CF-Timestamp: <unix 秒>
  X-CF-Nonce: <随机串>
  X-CF-Signature: <hex>
  meta = {"pipelineCode": "...", "mode": "EXECUTE" | "VALIDATE_ONLY",
          "idempotencyKey": "...", "callbackUrl": "https://<白名单域名>/..."}
GET  /open/v1/jobs/{jobId}    查询状态与问题
回调：POST callbackUrl，body = {"jobId", "status", "issues", "tables"}，带同样的签名头`;

/** P10 调用方管理（F15）。 */
export default function Clients() {
  const { message } = App.useApp();
  const [rows, setRows] = useState<any[]>([]);
  const [pipes, setPipes] = useState<any[]>([]);
  const [edit, setEdit] = useState<any>(null);
  const [form] = Form.useForm();
  const load = () => get<any[]>("/api/client-apps").then(setRows);
  useEffect(() => { load(); get<any[]>("/api/pipelines").then(setPipes); }, []);
  const open = (a: any) => {
    setEdit(a);
    form.setFieldsValue(a.id ? { ...a, callbackAllowlist: (a.callbackAllowlist || []).join("\n") } : { name: "", secretRef: "", allowedPipelines: [], callbackAllowlist: "", enabled: true, rateLimitPerMin: null });
  };
  const save = async () => {
    const v = await form.validateFields();
    const body = { ...v, callbackAllowlist: String(v.callbackAllowlist || "").split(/\s+/).filter(Boolean) };
    const r = await opCall({ title: edit.id ? `修改调用方 ${edit.name}` : "新建调用方", summary: `授权方案：${body.allowedPipelines.join("，") || "无"}；${body.enabled ? "启用" : "停用"}` },
      edit.id ? "PUT" : "POST", edit.id ? `/api/client-apps/${edit.id}` : "/api/client-apps", () => body).catch((e) => { message.error(e.message); return null; });
    if (r) { message.success(edit.id ? "已保存" : `已创建，AppKey：${r.appKey}`); setEdit(null); load(); }
  };
  return (
    <div className="cf-page">
      <Row gutter={16}>
        <Col span={16}>
          <Button type="primary" style={{ marginBottom: 12 }} id="new-client" onClick={() => open({})}>+ 新建调用方</Button>
          <Table rowKey="id" dataSource={rows} pagination={false} columns={[
            { title: "名称", dataIndex: "name" },
            { title: "AppKey", dataIndex: "appKey", render: (v) => <Typography.Text className="cf-mono" copyable>{v}</Typography.Text> },
            { title: "授权方案", dataIndex: "allowedPipelines", render: (v) => (v || []).map((c: string) => <Tag key={c}>{c}</Tag>) },
            { title: "限流/分钟", dataIndex: "rateLimitPerMin", render: (v) => v ?? "默认" },
            { title: "状态", dataIndex: "enabled", render: (v) => v ? <Tag color="green">启用</Tag> : <Tag>停用</Tag> },
            { title: "操作", render: (_: any, a: any) => <a onClick={() => open(a)}>编辑 🔒</a> },
          ]} />
        </Col>
        <Col span={8}>
          <h4>接入说明</h4>
          <Typography.Paragraph copyable={{ text: SAMPLE }}><pre style={{ fontSize: 12, background: "#fafafa", padding: 8, whiteSpace: "pre-wrap" }}>{SAMPLE}</pre></Typography.Paragraph>
        </Col>
      </Row>
      <Modal open={!!edit} width={560} title={edit?.id ? `编辑调用方（${edit.appKey}）` : "新建调用方"} onCancel={() => setEdit(null)} onOk={save} okText="保存… 🔒" cancelText="取消">
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input maxLength={64} /></Form.Item>
          <Form.Item name="secretRef" label="签名密钥引用名" extra="只填环境变量 / 密钥管理中的名称，服务端读取 CF_REF_<名称>" rules={[{ required: true, pattern: /^[A-Z][A-Z0-9_]*$/, message: "大写字母、数字、下划线" }]}><Input className="cf-mono" /></Form.Item>
          <Form.Item name="allowedPipelines" label="授权方案"><Select mode="multiple" options={pipes.map((p) => ({ value: p.code, label: `${p.code} ${p.name}` }))} /></Form.Item>
          <Form.Item name="callbackAllowlist" label="回调域名白名单（每行一个）"><Input.TextArea rows={2} className="cf-mono" /></Form.Item>
          <Space size="large">
            <Form.Item name="rateLimitPerMin" label="限流（次/分钟，空=系统默认）"><InputNumber min={1} /></Form.Item>
            <Form.Item name="enabled" label="启用" valuePropName="checked"><Switch /></Form.Item>
          </Space>
        </Form>
      </Modal>
    </div>
  );
}
