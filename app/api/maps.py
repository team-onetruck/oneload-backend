from fastapi import APIRouter

from app.api.deps import Maps
from app.domain.mapdata import MapData
from app.errors import ApiError, ErrorCode
from app.schemas import GraphOut, MapsOut, MapSummary, PoiOut, PoisOut

# Map data is public (API spec v0.3 §3), so these endpoints need no session token.
router = APIRouter(prefix="/maps", tags=["maps"])


def _get_map(maps: Maps, map_id: str) -> MapData:
    map_ = maps.get(map_id)
    if map_ is None:
        raise ApiError(ErrorCode.MAP_NOT_FOUND, f"Unknown map {map_id}.")
    return map_


@router.get("")
def list_maps(maps: Maps) -> MapsOut:
    return MapsOut(
        maps=[
            MapSummary(map_id=m.map_id, name=m.name, version=m.version, floors=m.floors, ar_supported=False)
            for m in maps.all()
        ]
    )


@router.get("/{map_id}/graph", response_model_by_alias=True)
def get_graph(map_id: str, maps: Maps) -> GraphOut:
    m = _get_map(maps, map_id)
    return GraphOut(
        map_id=m.map_id,
        map_version=m.version,
        coordinate_system=m.coordinate_system,
        floors=m.floors,
        nodes=m.nodes,
        edges=m.edges,
        landmarks=m.landmarks,
        panoramas=m.panoramas,
    )


@router.get("/{map_id}/pois")
def search_pois(map_id: str, maps: Maps, query: str = "") -> PoisOut:
    m = _get_map(maps, map_id)
    return PoisOut(
        map_id=m.map_id,
        map_version=m.version,
        pois=[
            PoiOut(
                poi_id=p.poi_id,
                name_ko=p.name.ko,
                name_en=p.name.en,
                category=p.category,
                train_type=p.train_type,
                floor_id=m.node_by_id[p.node_id].floor_id,
                nearest_node_id=p.node_id,
                aliases=p.aliases,
                verified=p.verified,
            )
            for p in m.search_pois(query)
        ],
    )
