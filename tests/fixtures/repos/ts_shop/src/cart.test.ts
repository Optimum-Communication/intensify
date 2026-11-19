import { Cart, discount } from "./index";

describe("Cart", () => {
  it("adds items", () => {
    const cart = new Cart("ana");
    cart.add("sku", 100);
    expect(cart.total()).toBe(100);
    expect(discount(10, "HALF")).toBe(5);
  });
});
