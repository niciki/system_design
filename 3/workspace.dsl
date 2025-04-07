workspace {
    model {
        user = person "User"
        
        auth = softwareSystem "Authentication Service" "auth" {
            AuthApi = container "Auth API" "authApi" {
                description "Handles authentication"
                technology "FastAPI"
            }
            AuthDb = container "Auth Database" "authDb" {
                description "Stores user data"
                technology "PostgreSQL"
            }
            AuthApi -> AuthDb "Reads/writes"
        }
        
        order = softwareSystem "Order Service" "order" {
            orderApi = container "Order API" "orderApi" {
                description "Processes orders"
                technology "FastAPI"
            }
            OrderDb = container "Order Database" "orderDb" {
                description "Stores orders"
                technology "PostgreSQL"
            }
            orderApi -> OrderDb "Reads/writes"
        }
        
        // Relationships
        user -> auth "Logs in"
        auth -> user "Returns JWT"
        
        user -> order "Submits order"
        order -> auth "Validates token"
        order -> user "Confirms order"
    }

}