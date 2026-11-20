package com.shop;

import jakarta.persistence.EntityManager;
import java.sql.Connection;

public class Catalog {
    private final ProductRepository products;
    private final EntityManager em;

    public Catalog(ProductRepository products, EntityManager em) {
        this.products = products;
        this.em = em;
    }

    public Product byId(long id) {
        return em.find(Product.class, id);
    }

    public void add(Product p) {
        products.save(p);
    }

    public long expensive() {
        return em.createQuery("select count(p) from Product p where p.price > 100", Long.class)
            .getSingleResult();
    }

    public void reprice(Connection c) throws Exception {
        c.prepareStatement("UPDATE products SET price = price * 2 "
            + "WHERE id IN (SELECT product_id FROM stock_levels)").executeUpdate();
    }
}
