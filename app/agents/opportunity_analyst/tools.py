from typing import Any
from uuid import UUID

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool

from app.agents.context import CRMContext
from app.database import CRMRepository


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def build_opportunity_tools(repository: CRMRepository) -> tuple[BaseTool, ...]:
    """Create the analyst's private read-only toolset."""

    @tool
    async def search_customers(
        runtime: ToolRuntime[CRMContext],
        query: str | None = None,
        status: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Search customers for opportunity analysis without changing data."""
        customers = await repository.list_customers(
            runtime.context.current_user(),
            query=query,
            status=status,
            limit=min(max(limit, 1), 50),
        )
        return {"count": len(customers), "customers": [_dump(item) for item in customers]}

    @tool
    async def get_customer(
        customer_id: str,
        runtime: ToolRuntime[CRMContext],
    ) -> dict[str, Any]:
        """Read one customer for opportunity analysis."""
        try:
            customer = await repository.get_customer(
                runtime.context.current_user(), UUID(customer_id)
            )
        except ValueError:
            return {"ok": False, "error": "customer_id 不是有效 UUID"}
        return {"ok": customer is not None, "customer": _dump(customer) if customer else None}

    @tool
    async def get_customer_pipeline_summary(
        runtime: ToolRuntime[CRMContext],
        limit: int = 20,
    ) -> dict[str, Any]:
        """Get a read-only pipeline aggregate and recently updated customers."""
        customers = await repository.list_customers(
            runtime.context.current_user(), limit=min(max(limit, 1), 50)
        )
        status_counts: dict[str, int] = {}
        for customer in customers:
            status_counts[customer.status] = status_counts.get(customer.status, 0) + 1
        return {
            "count": len(customers),
            "status_counts": status_counts,
            "recent_customers": [_dump(item) for item in customers],
        }

    return search_customers, get_customer, get_customer_pipeline_summary
