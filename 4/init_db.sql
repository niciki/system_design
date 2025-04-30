CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Устанавливаем соединение с БД order_service
\c order_service

-- Таблица пользователей
CREATE TABLE IF NOT EXISTS users (
    user_id SERIAL PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    password_hash VARCHAR(128) NOT NULL,
    full_name VARCHAR(100),
    role VARCHAR(20) NOT NULL,
    disabled BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CHECK (role IN ('client', 'admin', 'courier', 'warehouse_manager'))
);

-- Таблица адресов
CREATE TABLE IF NOT EXISTS addresses (
    address_id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(user_id) ON DELETE CASCADE,
    street VARCHAR(100) NOT NULL,
    city VARCHAR(50) NOT NULL,
    postal_code VARCHAR(20) NOT NULL,
    country VARCHAR(50) NOT NULL,
    is_default BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Таблица заказов
CREATE TABLE IF NOT EXISTS orders (
    order_id SERIAL PRIMARY KEY,
    client_id INTEGER REFERENCES users(user_id) NOT NULL,
    courier_id INTEGER REFERENCES users(user_id),
    total_amount NUMERIC(10, 2) NOT NULL,
    status VARCHAR(20) NOT NULL,
    payment_method VARCHAR(20) NOT NULL,
    delivery_type VARCHAR(20) NOT NULL,
    delivery_address_id INTEGER REFERENCES addresses(address_id),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    delivered_at TIMESTAMP WITH TIME ZONE,
    estimated_delivery DATE,
    notes TEXT,
    CHECK (status IN ('created', 'processing', 'in_transit', 'delivered', 'cancelled')),
    CHECK (payment_method IN ('cash', 'card', 'online')),
    CHECK (delivery_type IN ('standard', 'express', 'pickup'))
);

-- Таблица элементов заказа
CREATE TABLE IF NOT EXISTS order_items (
    item_id SERIAL PRIMARY KEY,
    order_id INTEGER REFERENCES orders(order_id) ON DELETE CASCADE NOT NULL,
    product_id INTEGER NOT NULL,
    name VARCHAR(100) NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    price NUMERIC(10, 2) NOT NULL CHECK (price > 0)
);

-- Создаем индексы
CREATE INDEX IF NOT EXISTS idx_orders_client_id ON orders(client_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_order_items_order_id ON order_items(order_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_lower ON users(LOWER(username));
CREATE INDEX IF NOT EXISTS idx_addresses_user_id ON addresses(user_id);

-- Вставляем тестовые данные
INSERT INTO users (username, password_hash, full_name, role)
VALUES 
    ('admin', crypt('admin123', gen_salt('bf')), 'Admin User', 'admin')
ON CONFLICT (username) DO NOTHING;

INSERT INTO users (username, password_hash, full_name, role)
VALUES 
    ('client1', crypt('client123', gen_salt('bf')), 'John Client', 'client')
ON CONFLICT (username) DO NOTHING;

INSERT INTO addresses (user_id, street, city, postal_code, country, is_default)
VALUES
    (2, '123 Main St', 'New York', '10001', 'USA', TRUE)
ON CONFLICT DO NOTHING;

INSERT INTO orders (client_id, total_amount, status, payment_method, delivery_type, delivery_address_id, estimated_delivery)
VALUES
    (2, 59.98, 'created', 'card', 'standard', 1, CURRENT_DATE + INTERVAL '3 days')
ON CONFLICT DO NOTHING;

INSERT INTO order_items (order_id, product_id, name, quantity, price)
VALUES
    (1, 101, 'Wireless Headphones', 1, 49.99)
ON CONFLICT DO NOTHING;
