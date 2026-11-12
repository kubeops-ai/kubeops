# kubeops
# Start the browser UI

## Start PostgreSQL for chat history

From the project root, start the PostgreSQL container:

```powershell
docker compose up -d postgres
```

Add this setting to the local `.env` file:

```env
KUBEOPS_POSTGRES_URL=postgresql://kubeops:kubeops_dev_password@localhost:5432/kubeops
```

LangGraph will create its checkpoint tables automatically when KubeOps starts.

Install the dependencies and start the FastAPI application:

```powershell
python -m pip install -r requirements.txt
python .\app.py
```

Open `http://127.0.0.1:8200` in a browser. Questions submitted from the page
use the same Supervisor, Insight, Troubleshooting, RAG, and MCP flow as
`ask_model.py`.

# Run the live pod-listing evaluation

With the MCP server running:

```powershell
python .\eval_list_pods.py
```

This verifies that `list_pods` is discovered, receives `namespace="dev"`, and
returns a list without changing the cluster. It also verifies that a direct
destructive command such as `kubectl delete deployment` is blocked by the
project guardrail.

