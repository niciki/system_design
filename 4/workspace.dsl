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

        analytics = softwareSystem "Event Tracking Service" "analytics" {
            eventsApi = container "Events API" "eventsApi" {
                description "Receives and processes user events"
                technology "FastAPI"
            }
            eventsDb = container "Events Database" "eventsDb" {
                description "Stores raw event data"
                technology "MongoDB"
            }
            eventsApi -> eventsDb "Writes events"
        }
        
        auth -> user "Returns JWT"
        
        user -> order "Submits order"
        order -> auth "Validates token"
        order -> user "Confirms order"

        user -> eventsApi "Sends events (e.g., clicks, page views)"
        eventsApi -> auth "Validates token"
    }
}