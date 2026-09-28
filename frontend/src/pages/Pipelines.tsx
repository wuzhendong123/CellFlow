import { App, Button, Empty, Form, Input, Modal, Select, Space, Table, Upload } from "antd";
import { useEffect, useState } from "react";
import { get, post, upload } from "../api";
import { JobStatus, PipelineStatus, fmtTime } from "../components/StatusTag";
import { navigate } from "../router";

/** P1 方案列表 + P2 新建方案 */
export default function Pipelines() {
  const { message } = App.useApp();
  const [rows, setRows] = useState<any[]>([]);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState<string | undefined>();
  const [dsId, setDsId] = useState<number | undefined>();
  const [dss, setDss] = useState<any[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);

  const load = () => {
    setLoading(true);
    const qs = new URLSearchParams();
    if (q) qs.set("q", q);
    if (status) qs.set("status", status);
    if (dsId) qs.set("datasourceId", String(dsId));
    get<any[]>(`/api/pipelines?${qs}`).then(setRows).finally(() => setLoading(false));
  };
  useEffect(load, [q, status, dsId]);
  useEffect(() => { get<any[]>("/api/datasources").then(setDss); }, []);

  return (
    <div className="cf-page">
      <Space style={{ marginBottom: 12 }}>
        <Input.Search placeholder="按编码/名称搜索" allowClear onSearch={setQ} style={{ width: 240 }} />
        <Select placeholder="状态" allowClear style={{ width: 120 }} value={status} onChange={setStatus}
          options={[{ value: "NORMAL", label: "正常" }, { value: "FROZEN", label: "已冻结" }, { value: "UNPUBLISHED", label: "未发布" }]} />
        <Select placeholder="数据源" allowClear style={{ width: 160 }} value={dsId} onChange={setDsId}
          options={dss.map((d) => ({ value: d.id, label: d.name }))} />
      </Space>
      <Button type="primary" id="new-pipeline" style={{ float: "right" }} onClick={() => setOpen(true)}>+ 新建方案</Button>
      <Table
        rowKey="id"
        loading={loading}
        dataSource={rows}
        pagination={{ pageSize: 20 }}
        locale={{ emptyText: <Empty description="还没有方案"><Button type="primary" onClick={() => setOpen(true)}>新建第一个方案</Button></Empty> }}
        onRow={(r) => ({ onClick: () => navigate(`/pipelines/${r.id}/canvas`), style: { cursor: "pointer" } })}
        columns={[
          { title: "方案编码", dataIndex: "code", render: (v) => <span className="cf-mono">{v}</span> },
          { title: "名称", dataIndex: "name" },
          { title: "数据源", dataIndex: "datasourceName" },
          { title: "生效版本", dataIndex: "publishedRev", render: (v) => (v ? `v${v}` : "—") },
          { title: "草稿", dataIndex: "draftChanged", render: (v) => (v ? <span className="cf-warn">● 有未发布修改</span> : "") },
          { title: "状态", dataIndex: "status", render: (v) => <PipelineStatus s={v} /> },
          { title: "最近任务", dataIndex: "lastJob", render: (v) => (v ? <Space><JobStatus s={v.status} />{fmtTime(v.submittedAt)}</Space> : "—") },
        ]}
      />
      <CreateModal open={open} dss={dss} onClose={() => setOpen(false)} onCreated={(p) => { message.success("方案已创建"); navigate(`/pipelines/${p.id}/canvas`); }} />
    </div>
  );
}

function CreateModal({ open, dss, onClose, onCreated }: { open: boolean; dss: any[]; onClose: () => void; onCreated: (p: any) => void }) {
  const [form] = Form.useForm();
  const [file, setFile] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const { message } = App.useApp();
  return (
    <Modal title="新建方案" open={open} onCancel={onClose} okText="创建" confirmLoading={busy} destroyOnHidden
      onOk={async () => {
        const v = await form.validateFields();
        setBusy(true);
        try {
          onCreated(await post("/api/pipelines", { ...v, sampleFileId: file?.fileId }));
        } catch (e: any) {
          message.error(e.message);
        } finally {
          setBusy(false);
        }
      }}>
      <Form form={form} layout="vertical" preserve={false}>
        <Form.Item name="code" label="方案编码" extra="业务服务靠它调用，创建后不可修改" validateDebounce={400}
          rules={[
            { required: true, message: "必填" },
            { pattern: /^[a-z][a-z0-9_]{1,63}$/, message: "小写字母、数字、下划线，以字母开头" },
            { validator: async (_, v) => { if (v && /^[a-z][a-z0-9_]{1,63}$/.test(v)) { const r = await get<any>(`/api/pipelines/code-available?code=${v}`); if (!r.available) throw new Error("编码已存在"); } } },
          ]}>
          <Input id="new-code" />
        </Form.Item>
        <Form.Item name="name" label="方案名称" rules={[{ required: true, message: "必填" }]}>
          <Input id="new-name" />
        </Form.Item>
        <Form.Item name="datasourceId" label="数据源" rules={[{ required: true, message: "必选" }]} extra="该方案所有目标表都必须在此数据源内">
          <Select id="new-ds" options={dss.map((d) => ({ value: d.id, label: `${d.name}（${d.dbName}）` }))} />
        </Form.Item>
        <Form.Item label="样例文件" extra="选填，可稍后在工作台上传；方案内所有源节点共用这一个文件">
          <Upload accept=".xlsx,.xlsm" maxCount={1} customRequest={async ({ file, onSuccess, onError }) => {
            try { const r = await upload(file as File); setFile(r); onSuccess?.(r); } catch (e: any) { message.error(e.message); onError?.(e); }
          }}>
            <Button>选择 xlsx</Button>
          </Upload>
        </Form.Item>
        <Form.Item name="description" label="说明"><Input.TextArea rows={2} /></Form.Item>
      </Form>
    </Modal>
  );
}
