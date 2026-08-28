from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def benchmark_status():
    """Benchmarking endpoints entry point."""
    return {"status": "benchmarking enabled"}
