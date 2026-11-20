import { applyDiscount, subtotal } from "@/pricing";
import * as log from "./util/log.js";

export class CartError extends Error {}

export class Cart {
  private items: Array<[string, number]> = [];

  constructor(readonly owner: string) {}

  add(sku: string, cents: number, qty = 1): void {
    if (qty <= 0) {
      throw new CartError("quantity must be positive");
    }
    for (let i = 0; i < qty; i++) this.items.push([sku, cents]);
    log.info(`added ${sku}`);
  }

  total(code?: string): number {
    const sum = subtotal(this.items);
    return code ? applyDiscount(sum, code) : sum;
  }

  #debug(): string {
    return JSON.stringify(this.items);
  }
}
