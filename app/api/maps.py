from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import Maps
from app.domain.mapdata import MapData
from app.errors import ApiError, ErrorCode
from app.openapi import Tag
from app.schemas import GraphOut, MapsOut, MapSummary, PoiOut, PoisOut

# Map data is public (API spec v0.3 §3), so these endpoints need no session token.
router = APIRouter(prefix="/maps", tags=[Tag.MAPS])


def _get_map(maps: Maps, map_id: str) -> MapData:
    map_ = maps.get(map_id)
    if map_ is None:
        raise ApiError(ErrorCode.MAP_NOT_FOUND, f"Unknown map {map_id}.")
    return map_


@router.get("", summary="지도 목록", description="사용할 수 있는 지도와 버전, 층 목록입니다.")
def list_maps(maps: Maps) -> MapsOut:
    return MapsOut(
        maps=[
            MapSummary(map_id=m.map_id, name=m.name, version=m.version, floors=m.floors, ar_supported=False)
            for m in maps.all()
        ]
    )


@router.get(
    "/{map_id}/graph",
    response_model_by_alias=True,
    summary="지도 그래프 내려받기",
    description="노드·통로(`edges`)·표지판(`landmarks`)·거리뷰 사진(`panoramas`)을 한 번에 받습니다."
    " 좌표·방향 규약은 `coordinate_system`을 따릅니다.",
)
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


@router.get(
    "/{map_id}/pois",
    summary="목적지(POI) 검색",
    description="수동 목적지 선택 화면용 검색입니다. 확인된(`verified`) 장소만 돌려주며,"
    " 이름(국/영)과 별칭에서 찾습니다.",
)
def search_pois(
    map_id: str, maps: Maps, query: Annotated[str, Query(description="검색어, 예: 직통, express. 비우면 전체")] = ""
) -> PoisOut:
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
