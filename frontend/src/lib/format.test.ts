import { describe, expect, it } from "vitest";
import { perUsd } from "./format";

describe("perUsd（查看汇率组：币种:美元 = X:1）", () => {
  it.each([
    ["0.128", "7.81"], // 港币
    ["0.0067", "149.25"], // 日元
    ["0.00072", "1,388.89"], // 韩元
    ["1", "1.00"], // 美元
    ["0.56", "1.79"], // 纽币
    ["1.08", "0.93"], // 欧元
    ["0.125", "8.00"],
    ["0.6", "1.67"],
  ])("%s USD → %s", (v, want) => expect(perUsd(v)).toBe(want));

  it("零值不做除法", () => expect(perUsd("0")).toBe("—"));
});
