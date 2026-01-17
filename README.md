# How to run this

## Start Minikube

minikube start

## Install kube-prometheus-stack and mongodb exporter

helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

helm install prometheus prometheus-community/kube-prometheus-stack \
 --namespace monitoring --create-namespace \
 -f kubernetes/alertmanager/alertmanager-values.yaml

## Deploy app stack

kubectl apply -f kubernetes/mongo/mongodb.yaml
kubectl apply -f kubernetes/app/deployment.yaml
kubectl apply -f kubernetes/prometheus/prometheusrule.yaml

## Install mongodb-exporter

helm install mongo-exporter prometheus-community/prometheus-mongodb-exporter --namespace todo-fastapi-mongodb ----create-namespace \
 -f helm/mongodb-exporter/mongodb-exporter-values.yaml

## Verify all pods are running

kubectl get pods -n todo-fastapi-mongodb
kubectl get pods -n monitoring

Step 1: Setup Port Forwarding (3 Terminals)
Open 3 terminal windows:
Terminal 1 - Todo App:
bashkubectl port-forward svc/todo-app-service 3000:80 -n todo-fastapi-mongodb
Terminal 2 - Prometheus:
bashkubectl port-forward -n monitoring svc/prometheus-kube-prometheus-prometheus 9090:9090
Terminal 3 - AlertManager:
bashkubectl port-forward -n monitoring svc/prometheus-kube-prometheus-alertmanager 9093:9093

# Testing

Scale down to 0 replicas
kubectl scale deployment/mongodb --replicas=0 -n todo-fastapi-mongodb

Verify scaling
kubectl get pods -n todo-fastapi-mongodb # mongodb pods should disappear

Monitor
Prometheus (http://localhost:9090/alerts):

MongoDBInstanceDown appears
Status: FIRING 🔴

AlertManager (http://localhost:9093):

Alert shows severity: critical
Team label: devops
now the slack notification is delievered
Expected Notification
Slack Notification:
[FIRING] MongoDBInstanceDown
─────────────────────────────
Namespace: todo-fastapi-mongodb
Alert Name: MongoDBInstanceDown
Status: firing
Number of Alerts: 1

Alert: MongoDB instance is down for 5 second
Instance: mongodb-xxxxx
Started: 2024-01-18 15:35:20
Severity: critical

## Notes

Application + Exporter in the SAME namespace
Prometheus in a CENTRAL monitoring namespace

Pod starts
→ App starts
→ Tries to connect to MongoDB
→ Mongo is NOT running
→ Exception raised
→ FastAPI exits
→ Kubernetes restarts pod
→ Liveness probe fails
→ Infinite restart loop

Install the MongoDB exporter in the same namespace as MongoDB (todo-fastapi-mongodb), and let Prometheus (in monitoring) scrape it via a ServiceMonitor.

monitoring namespace
└── Prometheus (kube-prometheus-stack)

todo-fastapi-mongodb namespace
├── MongoDB Pod
├── MongoDB Exporter Pod
├── MongoDB Service
└── ServiceMonitor (can be here OR monitoring ns)

prometheus only checks the alert rules once the rules are met it triggers the alert manager which takes care of entire alert lifecycle

applying the prometheus rule
kubectl apply -f kubernetes/prometheus/prometheusrule.yaml

upgrade helm prometheus to use custom alertmanager values
helm upgrade prometheus prometheus-community/kube-prometheus-stack -n monitoring -f kubernetes/alertmanager/alertmanager-values.yaml

Helm values
↓
Kubernetes Secret
↓
Alertmanager Pod
↓
Slack / Email / PagerDuty

Alertmanager routing ALWAYS lives in a Secret

check the alert rules
kubectl get secret -n monitoring alertmanager-prometheus-kube-prometheus-alertmanager ` -o jsonpath="{.data.alertmanager\.yaml}" |% { [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($\_)) }

why job name in prometheus is service name not service monitor name
Job label comes from the scrape config, not the ServiceMonitor name

Your ServiceMonitor is named todo-app-monitor (just a name)
But Prometheus generates the job label from the Service name: todo-app-service

Prometheus relabeling adds the job label

When ServiceMonitor scrapes, it uses the Service name as the job
You can see this in your metrics: job="todo-app-service"

🕒 Alert Lifecycle Timeline
| Phase | Duration | Cumulative Time | Action |
| ------------------ | -------- | --------------- | ---------------------------------------- |
| Scrape fails | Instant | T = 0s | App becomes unreachable |
| Evaluation window | 30s | T = 30s | Prometheus confirms condition for 30s |
| Alert **PENDING** | Instant | T = 30s | Alert status changes to PENDING |
| Alert **FIRING** | Instant | T = 30s | Alert confirmed and sent to Alertmanager |
| Grouping wait | 10s | T = 40s | Alertmanager groups/batches alerts |
| First notification | Instant | T = 40s | Slack notification sent |

🔢 Key Configuration Numbers
| Metric | Value | Purpose |
| ----------------------------- | ----- | --------------------------------------------------- |
| `interval` | 30s | How often Prometheus evaluates the rule |
| `for` | 30s | How long the condition must stay true before firing |
| `group_wait` | 10s | How long Alertmanager waits to batch alerts |
| **Total time to first alert** | ~40s | Time from app down → Slack notification |

Inhibit rules suppress less important alerts when a related, more important alert is firing — for example: if MongoDBInstanceDown is firing, suppress TodoAppDown alerts in the same namespace so you don’t get duplicate alerts.
