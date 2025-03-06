workspace {
    name "Сервис доставки"
    description "Система управления пользователями, посылками и доставками"

    !identifiers hierarchical

    model {
        admin = person "Администратор" {
            description "Управление пользователями, посылками и доставками. Управление статусами доставок."
        }

        client = person "Клиент (отправитель/получатель)" {
            description "Регистрация и управление профилем, создание посылок, просмотр своих посылок и доставок, отслеживание статуса доставки."
        }

        courier = person "Курьер" {
            description "Просмотр назначенных доставок, обновление статуса доставки (например, 'в пути', 'доставлено')."
        }

        warehouseManager = person "Менеджер склада" {
            description "Управление посылками на складе, обновление статусов посылок (например, 'на складе', 'отправлено')."
        }

        deliverySystem = softwareSystem "Сервис доставки" {
            description "Система управления пользователями, посылками и доставками"

            apiGateway = container "API Gateway" {
                technology "Go, Gin"
                description "API-шлюз для маршрутизации запросов"
            }

            webApp = container "Web Application" {
                technology "Go, HTML, CSS, JavaScript"
                description "Веб-приложение для взаимодействия пользователей с системой"
                -> apiGateway "Передача запросов" HTTPS
            }

            userDb = container "User Database" {
                technology "PostgreSQL"
                description "База данных для хранения информации о пользователях"
            }

            packageDb = container "Package Database" {
                technology "PostgreSQL"
                description "База данных для хранения информации о посылках"
            }

            deliveryDb = container "Delivery Database" {
                technology "PostgreSQL"
                description "База данных для хранения информации о доставках"
            }

            userService = container "User Service" {
                technology "Go"
                description "Сервис управления пользователями"
                -> apiGateway "Запросы на управление пользователями" HTTPS
                -> userDb "Хранение информации о пользователях" SQL
            }

            packageService = container "Package Service" {
                technology "Go"
                description "Сервис управления посылками"
                -> apiGateway "Запросы на управление посылками" HTTPS
                -> packageDb "Хранение информации о посылках" SQL
            }

            deliveryService = container "Delivery Service" {
                technology "Go"
                description "Сервис управления доставками"
                -> apiGateway "Запросы на управление доставками" HTTPS
                -> deliveryDb "Хранение информации о доставках" SQL
            }

            notificationService = container "Notification Service" {
                technology "Go"
                description "Сервис отправки уведомлений (SMS, email, push)"
                -> apiGateway "Запросы на отправку уведомлений" HTTPS
            }

            paymentService = container "Payment Service" {
                technology "Go"
                description "Сервис обработки платежей"
                -> apiGateway "Запросы на обработку платежей" HTTPS
            }

            trackingService = container "Tracking Service" {
                technology "Go"
                description "Сервис отслеживания доставок (интеграция с GPS)"
                -> apiGateway "Запросы на отслеживание доставок" HTTPS
            }
        }

        authSystem = softwareSystem "Система аутентификации и авторизации" {
            description "Управление пользователями и их ролями. Обеспечение безопасности API."
        }

        paymentGateway = softwareSystem "Платежная система" {
            description "Обработка оплаты за доставку. Интеграция с банковскими системами или платежными шлюзами."
        }

        gpsTracking = softwareSystem "Система отслеживания доставок" {
            description "Предоставление информации о текущем статусе доставки. Интеграция с GPS."
        }

        notificationGateway = softwareSystem "Система уведомлений" {
            description "Отправка уведомлений пользователям (SMS, email, push-уведомления) о статусе доставки."
        }

        warehouseSystem = softwareSystem "Складская система" {
            description "Управление и перемещение посылок на складе."
        }

        admin -> deliverySystem.webApp "Управление пользователями, посылками и доставками"
        client -> deliverySystem.webApp "Регистрация, создание посылок, отслеживание доставок"
        courier -> deliverySystem.webApp "Просмотр и обновление статуса доставок"
        warehouseManager -> deliverySystem.webApp "Управление посылками на складе"

        deliverySystem.userService -> authSystem "Аутентификация и авторизация" HTTPS
        deliverySystem.paymentService -> paymentGateway "Обработка платежей" HTTPS
        deliverySystem.trackingService -> gpsTracking "Отслеживание доставок" HTTPS
        deliverySystem.notificationService -> notificationGateway "Отправка уведомлений" HTTPS
        deliverySystem.packageService -> warehouseSystem "Управление посылками на складе" HTTPS
    }

    views {
        systemContext deliverySystem {
            include *
            autolayout lr
        }

        container deliverySystem {
            include *
            autolayout lr
        }

        dynamic deliverySystem "createUser" "Создание нового пользователя" {
            admin -> deliverySystem.webApp "Создание нового пользователя"
            deliverySystem.webApp -> deliverySystem.apiGateway "POST /user"
            deliverySystem.apiGateway -> deliverySystem.userService "Создает запись в базе данных"
            deliverySystem.userService -> deliverySystem.userDb "INSERT INTO users"
            autolayout lr
        }

        dynamic deliverySystem "createPackage" "Создание новой посылки" {
            client -> deliverySystem.webApp "Создание новой посылки"
            deliverySystem.webApp -> deliverySystem.apiGateway "POST /package"
            deliverySystem.apiGateway -> deliverySystem.packageService "Создает запись о посылке"
            deliverySystem.packageService -> deliverySystem.packageDb "INSERT INTO packages"
            autolayout lr
        }

        dynamic deliverySystem "updateDeliveryStatus" "Обновление статуса доставки" {
            courier -> deliverySystem.webApp "Обновление статуса доставки"
            deliverySystem.webApp -> deliverySystem.apiGateway "PUT /delivery/{id}/status"
            deliverySystem.apiGateway -> deliverySystem.deliveryService "Обновляет статус доставки"
            deliverySystem.deliveryService -> deliverySystem.deliveryDb "UPDATE deliveries SET status={status}"
            autolayout lr
        }

        dynamic deliverySystem "trackDelivery" "Отслеживание доставки" {
            client -> deliverySystem.webApp "Запрашивает статус доставки"
            deliverySystem.webApp -> deliverySystem.apiGateway "GET /delivery/{id}/status"
            deliverySystem.apiGateway -> deliverySystem.trackingService "Получает текущий статус доставки"
            deliverySystem.trackingService -> gpsTracking "Запрашивает данные о местоположении"
            autolayout lr
        }

        theme default
    }
}
