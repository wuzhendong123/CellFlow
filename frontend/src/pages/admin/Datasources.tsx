import { Alert, App, Button, Form, Input, InputNumber, Modal, Radio, Space, Table, Tag } from "antd";
import { useEffect, useState } from "react";
import { get, opCall, post } from "../../api";

/** P9 数据源管理（F15）：只填环境变量引用名，页面不出现真实地址和密码。 */
export default function Datasources() {
  const { message } = App.useApp();
  const [rows, setRows] = useState<any[]>([]);
  const [edit, setEdit] = useState<any>(null);
  const [tests, setTests] = useState<Record<number, any>>({});
  const [form] = Form.useForm();
  const load = () => get<any[]>("/api/datasources").then(setRows);
  useEffect(() => { load(); }, []);
  const open = (d: any) => {
    setEdit(d);
    form.resetFields();
    form.setFieldsValue(d.id ? { ...d, password: "" } : { mode: "DIRECT", name: "", dbName: "", host: "", port: 3306, username: "", password: "", hostRef: "", credentialRef: "" });
  };
  const mode = Form.useWatch("mode", form);
  const where = (d: any) => (d.mode === "DIRECT" ? `${d.username}@${d.host}:${d.port}` : `引用 ${d.hostRef}`);
  const save = async () => {
    const v = await form.validateFields();
    const r = await opCall({ title: edit.id ? `修改数据源 ${edit.name}` : "新建数据源", summary: `名称 ${v.name}，库 ${v.dbName}，${v.mode === "DIRECT" ? `连接 ${v.username}@${v.host}:${v.port}${v.password ? "（口令已填写，将加密保存）" : "（口令不修改）"}` : `连接引用 ${v.hostRef}，凭证引用 ${v.credentialRef || "—"}`}` },
      edit.id ? "PUT" : "POST", edit.id ? `/api/datasources/${edit.id}` : "/api/datasources", () => v).catch((e) => { message.error(e.message); return null; });
    if (r) { message.success("已保存"); setEdit(null); load(); }
  };
  const del = async (d: any) => {
    const r = await opCall({ title: `删除数据源 ${d.name}`, summary: "删除后不可恢复（仅在没有方案使用时可删）。" }, "DELETE", `/api/datasources/${d.id}`)
      .then(() => true).catch((e) => { message.error(e.message); return null; });
    if (r) { message.success("已删除"); load(); }
  };
  const test = (d: any) => post(`/api/datasources/${d.id}/test`).then((r) => setTests({ ...tests, [d.id]: r })).catch((e) => message.error(e.message));
  return (
    <div className="cf-page">
      <Button type="primary" style={{ marginBottom: 12 }} id="new-datasource" onClick={() => open({})}>+ 新建数据源</Button>
      <Table rowKey="id" dataSource={rows} pagination={false} columns={[
        { title: "名称", dataIndex: "name" }, { title: "库名", dataIndex: "dbName" },
        { title: "连接", render: (_: any, d: any) => <span className="cf-mono">{where(d)}</span> },
        { title: "方式", dataIndex: "mode", render: (v) => (v === "DIRECT" ? <Tag>直接填写</Tag> : <Tag color="blue">环境变量引用</Tag>) },
        { title: "使用方案数", dataIndex: "pipelineCount" },
        { title: "连通性", render: (_: any, d: any) => {
          const t = tests[d.id];
          if (!t) return "—";
          return <Space size={2} wrap>{Object.entries<boolean>(t.checks).map(([k, ok]) => <Tag key={k} color={ok ? "green" : "red"} title={t.detail?.[k]}>{k}</Tag>)}</Space>;
        } },
        { title: "操作", render: (_: any, d: any) => <Space><a onClick={() => test(d)}>测试连接</a><a onClick={() => open(d)}>编辑 🔒</a><a className="cf-err" onClick={() => del(d)}>删除 🔒</a></Space> },
      ]} />
      <Modal open={!!edit} title={edit?.id ? "编辑数据源" : "新建数据源"} onCancel={() => setEdit(null)} onOk={save} okText="保存… 🔒" cancelText="取消">
        <Form form={form} layout="vertical">
          <Form.Item name="mode" label="连接方式">
            <Radio.Group options={[{ value: "DIRECT", label: "直接填写" }, { value: "REF", label: "环境变量引用（生产推荐）" }]} />
          </Form.Item>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input maxLength={64} /></Form.Item>
          <Form.Item name="dbName" label="库名" rules={[{ required: true }]}><Input maxLength={64} /></Form.Item>
          {mode === "REF" ? (
            <>
              <Alert type="info" showIcon style={{ marginBottom: 12 }} message="只填写环境变量 / 密钥管理中的名称（如 BIZ_MYSQL），服务端读取 CF_REF_<名称>，真实地址与密码不经过页面。" />
              <Form.Item name="hostRef" label="连接地址引用名" rules={[{ required: true, pattern: /^[A-Za-z][A-Za-z0-9_]*$/, message: "字母、数字、下划线" }]}><Input className="cf-mono" /></Form.Item>
              <Form.Item name="credentialRef" label="凭证引用名" rules={[{ pattern: /^[A-Za-z][A-Za-z0-9_]*$/, message: "字母、数字、下划线" }]}><Input className="cf-mono" /></Form.Item>
            </>
          ) : (
            <>
              <Alert type="info" showIcon style={{ marginBottom: 12 }} message="口令用服务端主密钥（CF_SECRET_KEY）加密保存，保存后不再显示。本机 Docker 里连接 Mac 上的 MySQL，主机填 host.docker.internal。" />
              <Space style={{ display: "flex" }} align="start">
                <Form.Item name="host" label="主机" rules={[{ required: true }]} style={{ flex: 1 }}><Input placeholder="10.0.0.5 / host.docker.internal / mysql" /></Form.Item>
                <Form.Item name="port" label="端口" rules={[{ required: true }]}><InputNumber min={1} max={65535} /></Form.Item>
              </Space>
              <Form.Item name="username" label="账号" rules={[{ required: true }]}><Input autoComplete="off" /></Form.Item>
              <Form.Item name="password" label="口令" rules={edit?.id && edit?.mode === "DIRECT" ? [] : [{ required: true, message: "请填写口令" }]}
                extra={edit?.id && edit?.mode === "DIRECT" ? "留空表示不修改" : undefined}>
                <Input.Password autoComplete="new-password" />
              </Form.Item>
            </>
          )}
        </Form>
      </Modal>
    </div>
  );
}
