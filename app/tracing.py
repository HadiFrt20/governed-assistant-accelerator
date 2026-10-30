"""MLflow Tracing for the assistant: one trace per user request, one span per step (router, LLM call, Genie One, Office generation).
No-op when MLFLOW_EXPERIMENT_ID is unset or mlflow is unavailable, so the agent never fails because of tracing."""
import os
import urllib.parse

EXPERIMENT = os.environ.get("MLFLOW_EXPERIMENT_ID", "")
ON = False
try:
    if EXPERIMENT:
        import mlflow
        mlflow.set_tracking_uri("databricks")
        mlflow.set_experiment(experiment_id=EXPERIMENT)
        ON = True
except Exception as e:  # tracing must never break the assistant
    print("TRACING_DISABLED", e, flush=True)


class _NoSpan:
    trace_id = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def set_inputs(self, *a): pass
    def set_outputs(self, *a): pass
    def set_attributes(self, *a): pass
    def set_attribute(self, *a): pass


def span(name, kind="UNKNOWN", **attributes):
    if not ON:
        return _NoSpan()
    try:
        return mlflow.start_span(name, span_type=kind, attributes=attributes)
    except Exception as e:
        print("TRACING_SPAN_ERROR", e, flush=True)
        return _NoSpan()


def tag_trace(**tags):
    if ON:
        try:
            mlflow.update_current_trace(tags={k: str(v) for k, v in tags.items() if v is not None})
        except Exception as e:
            print("TRACING_TAG_ERROR", e, flush=True)


def url(host, trace_id):
    return f"{host}/ml/experiments/{EXPERIMENT}/traces?selectedEvaluationId={urllib.parse.quote(trace_id, safe='')}" if trace_id else None
