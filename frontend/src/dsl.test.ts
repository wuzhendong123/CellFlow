import { describe, expect, it } from "vitest";
import { canConnect, colLetter, colNumber, Dsl, inputPorts, outputPorts, parseA1, toA1 } from "./dsl";

const dsl: Dsl = {
  nodes: [
    { id: "src", type: "EXCEL_SOURCE", config: { regions: [{ regionId: "a", outputPortId: "out_kv", shape: "KEY_VALUE" }, { regionId: "b", outputPortId: "out_d", shape: "DETAIL" }, { regionId: "c", shape: "IGNORE" }] } },
    { id: "d", type: "DERIVE", config: {} },
    { id: "v", type: "VALIDATOR", config: { refs: { item: "in_ref_item" } } },
  ],
  edges: [{ id: "e1", source: { nodeId: "src", portId: "out_d" }, target: { nodeId: "d", portId: "in" } }],
};
const single = (_n: string, p: string) => p === "out_kv";

describe("dsl", () => {
  it("A1 conversions", () => {
    expect(colLetter(1)).toBe("A");
    expect(colLetter(28)).toBe("AB");
    expect(colNumber("AB")).toBe(28);
    expect(parseA1("a17:e22")).toEqual({ startRow: 17, startCol: 1, endRow: 22, endCol: 5, a1: "A17:E22" });
    expect(parseA1("x")).toBeNull();
    expect(toA1({ startRow: 1, startCol: 1, endRow: 5, endCol: 2 })).toBe("A1:B5");
  });

  it("ports mirror backend", () => {
    expect(outputPorts(dsl.nodes[0]).map((p) => p.id)).toEqual(["out_kv", "out_d"]);
    expect(inputPorts(dsl.nodes[2]).map((p) => [p.id, p.kind])).toContainEqual(["in_ref_item", "REF"]);
  });

  it("connection rules (F4-5, F4-5a, F4-17)", () => {
    expect(canConnect(dsl, "d", "out", "src", "x", single)).toBe("源节点没有输入端口");
    expect(canConnect(dsl, "src", "out_d", "d", "in_params", single)).toMatch("单行");
    expect(canConnect(dsl, "src", "out_kv", "d", "in_params", single)).toBeNull();
    expect(canConnect(dsl, "src", "out_kv", "d", "in", single)).toBe("该端口只能接一条线");
    const cyc: Dsl = { ...dsl, edges: [...dsl.edges, { id: "e2", source: { nodeId: "d", portId: "out" }, target: { nodeId: "v", portId: "in_main" } }] };
    expect(canConnect(cyc, "v", "out_pass", "d", "in_params", () => true)).toBe("连线会形成环");
  });
});

describe("reconnect / replace", () => {
  it("单连接端口：默认拒绝，允许替换时通过；改接时忽略正在移动的线", () => {
    expect(canConnect(dsl, "src", "out_kv", "d", "in", single)).toBe("该端口只能接一条线");
    expect(canConnect(dsl, "src", "out_kv", "d", "in", single, { allowReplace: true })).toBeNull();
    expect(canConnect(dsl, "src", "out_kv", "d", "in", single, { ignoreEdgeId: "e1" })).toBeNull();
  });
});
