"""费用与告警（系统管理员）。"""

from __future__ import annotations

from ledger.application import costs

from .app import Request, Response, handles


@handles("getCosts")
def get_costs(req: Request) -> Response:
    return Response(200, costs.get_costs(req.ctx, req.actor, req.query.get("month")))


@handles("listAlerts")
def list_alerts(req: Request) -> Response:
    return Response(200, {"items": costs.list_alerts(req.ctx, req.actor, req.query.get("month"))})
