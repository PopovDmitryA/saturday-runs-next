"""Планировщик забегов: блок «Планируют пробежать», окно «Планирую», кабинет /plans,
ближайший план и календарь «Планы» в профиле.

Блок локации, даты старта, строка и календарь профиля публичны — их видят и
гости (имена, места и «Не бегаю» только публичных планов). Всё остальное —
свои планы, «Не бегаю», локации на дату для окна плана от даты
(/planner/locations) и постоянный план («каждую субботу», /planner/series) —
за логином. Публичность — у каждого плана (`is_public` в запросах записи).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_optional_user
from app.db.session import get_db
from app.models import User
from app.schemas.planner import (
    LocationPlansBlock,
    LocationStartDates,
    MyPlansResponse,
    MySeriesResponse,
    NextPlanResponse,
    NextStartDay,
    PlanItem,
    PlanLocation,
    PlanLocationsResponse,
    PlanSaveRequest,
    PlanSaveResponse,
    PlanSeries,
    ProfilePlanCalendar,
    ProfilePlanLine,
    ProfilePlansTab,
    PublicSpecialDate,
    PublicSpecialDatesResponse,
    RestConflictResponse,
    RestRequest,
    RestResponse,
    SeriesConflictResponse,
    SeriesHint,
    SeriesPreviewResponse,
    SeriesResumeResponse,
    SeriesSaveRequest,
    SeriesSaveResponse,
)
from app.services.plan_series_service import (
    SERIES_PREVIEW_DEFAULT,
    SERIES_PREVIEW_MAX,
    SeriesConflict,
    active_series,
    resume_series,
    save_series,
    series_hint,
    series_payload,
    series_preview,
    snooze_series_hint,
    stop_series,
)
from app.services.planner_locations_service import locations_for_date
from app.services.profile_slug_service import resolve_profile_handle
from app.services.run_plan_service import (
    PlanError,
    RestConflict,
    clear_rest,
    delete_plan,
    list_my_plans,
    location_plans_block,
    location_start_dates,
    next_plan,
    next_start_day_state,
    plan_item,
    plan_items,
    plan_save_context,
    profile_plan_calendar,
    profile_plan_line,
    profile_plans_tab,
    public_special_dates,
    set_rest,
    upsert_plan,
)
from app.services.start_calendar_service import moscow_today

router = APIRouter(prefix="/planner", tags=["planner"])

# Кабинет «Планы» грузит месяц и список «Ближайшие» на 120 дней.
MY_PLANS_MAX_RANGE_DAYS = 120
# Как далеко от сегодня: календарь листается на 12 месяцев в обе стороны, а
# сетка месяца захватывает соседние недели — с запасом.
MY_PLANS_REACH_DAYS = 450


def _raise(exc: PlanError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


@router.get("/location", response_model=LocationPlansBlock)
def get_location_plans(
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    viewer: Annotated[User | None, Depends(get_optional_user)],
    key: Annotated[str, Query(min_length=1, max_length=255)],
) -> LocationPlansBlock:
    # Ответ свой у каждого зрителя (его планы) и меняется по клику:
    # ни браузер, ни прокси его не кэшируют.
    response.headers["Cache-Control"] = "private, no-store"
    try:
        payload = location_plans_block(db, key, viewer)
    except PlanError as exc:
        raise _raise(exc) from exc
    return LocationPlansBlock.model_validate(payload)


@router.get("/location/dates", response_model=LocationStartDates)
def get_location_start_dates(
    db: Annotated[Session, Depends(get_db)],
    key: Annotated[str, Query(min_length=1, max_length=255)],
) -> LocationStartDates:
    try:
        payload = location_start_dates(db, key)
    except PlanError as exc:
        raise _raise(exc) from exc
    return LocationStartDates.model_validate(payload)


@router.get("/locations", response_model=PlanLocationsResponse)
def get_plan_locations(
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    on_date: Annotated[date, Query(alias="date")],
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> PlanLocationsResponse:
    """«Где бежите?» — окно плана от даты («Мои планы», «Ближайший план»):
    локации, на которые эту дату можно запланировать. Без q — подсказки
    (suggested), с q от двух знаков — поиск (results). Ответ свой у каждого
    (mine, regular) — мимо кэшей."""
    response.headers["Cache-Control"] = "private, no-store"
    try:
        payload = locations_for_date(db, user, on_date, q)
    except PlanError as exc:
        raise _raise(exc) from exc
    return PlanLocationsResponse.model_validate(payload)


@router.get("/profile", response_model=ProfilePlanLine | None)
def get_profile_plan(
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    viewer: Annotated[User | None, Depends(get_optional_user)],
    handle: Annotated[str, Query(min_length=1, max_length=64)],
) -> ProfilePlanLine | None:
    """Строка в шапке профиля: ближайший публичный план на любую дату. null —
    строки нет (публичных планов впереди нет, профиль закрыт, ребёнок).

    Отдельный запрос, как блок локации: ответ зависит от зрителя (хозяину —
    и приватный план), поэтому мимо кэшей, пререндера и OG-картинок.
    """
    response.headers["Cache-Control"] = "private, no-store"
    target = resolve_profile_handle(db, handle)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")
    payload = profile_plan_line(db, target, viewer)
    return ProfilePlanLine.model_validate(payload) if payload is not None else None


@router.get("/profile/tab", response_model=ProfilePlansTab)
def get_profile_plans_tab(
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    viewer: Annotated[User | None, Depends(get_optional_user)],
    handle: Annotated[str, Query(min_length=1, max_length=64)],
) -> ProfilePlansTab:
    """Активна ли вкладка «Планы» профиля: есть ли у человека будущие
    публичные планы или публичное «Не бегаю». Зависит от зрителя (хозяину —
    всегда), поэтому мимо кэшей, как строка профиля."""
    response.headers["Cache-Control"] = "private, no-store"
    target = resolve_profile_handle(db, handle)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")
    return ProfilePlansTab(state=profile_plans_tab(db, target, viewer))


def _parse_month(raw: str) -> date:
    try:
        year, month = (int(part) for part in raw.split("-"))
        return date(year, month, 1)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Месяц — в виде ГГГГ-ММ") from exc


@router.get("/profile/calendar", response_model=ProfilePlanCalendar)
def get_profile_plan_calendar(
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    viewer: Annotated[User | None, Depends(get_optional_user)],
    handle: Annotated[str, Query(min_length=1, max_length=64)],
    month: Annotated[str, Query(pattern=r"^\d{4}-\d{2}$")],
) -> ProfilePlanCalendar:
    """Вкладка «Планы» профиля: публичные планы и публичные «Не бегаю» по дням
    месяца, с сегодняшнего. Закрытый профиль — 404 (хозяину — пустой
    календарь). Мимо кэшей, пререндера и OG, как строка профиля: план меняется
    по клику, а публичность — у каждого плана своя."""
    response.headers["Cache-Control"] = "private, no-store"
    target = resolve_profile_handle(db, handle)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")
    try:
        payload = profile_plan_calendar(db, target, viewer, _parse_month(month))
    except PlanError as exc:
        raise _raise(exc) from exc
    return ProfilePlanCalendar.model_validate(payload)


@router.get("/special-dates", response_model=PublicSpecialDatesResponse)
def get_special_dates(
    db: Annotated[Session, Depends(get_db)],
    date_from: Annotated[date, Query(alias="from")],
    date_to: Annotated[date, Query(alias="to")],
) -> PublicSpecialDatesResponse:
    """Особые даты стартов (Новый год и праздники в будни) — публично, без входа:
    это часть календаря стартов, а не чьи-то данные."""
    try:
        items = public_special_dates(db, date_from, date_to)
    except PlanError as exc:
        raise _raise(exc) from exc
    return PublicSpecialDatesResponse(items=[PublicSpecialDate.model_validate(item) for item in items])


@router.get("/my", response_model=MyPlansResponse)
def get_my_plans(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    date_from: Annotated[date, Query(alias="from")],
    date_to: Annotated[date, Query(alias="to")],
) -> MyPlansResponse:
    if date_to < date_from or (date_to - date_from).days > MY_PLANS_MAX_RANGE_DAYS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Диапазон дат — не больше {MY_PLANS_MAX_RANGE_DAYS} дней",
        )
    today = moscow_today()
    if date_from < today - timedelta(days=MY_PLANS_REACH_DAYS) or date_to > today + timedelta(days=MY_PLANS_REACH_DAYS):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Планы листаются на год назад и на год вперёд",
        )
    items = list_my_plans(db, user, date_from, date_to)
    return MyPlansResponse(today=today, items=[PlanItem.model_validate(item) for item in items])


@router.get("/next", response_model=NextPlanResponse)
def get_next_plan(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> NextPlanResponse:
    item = next_plan(db, user)
    current = active_series(db, user.id)
    hint = series_hint(db, user)
    return NextPlanResponse(
        plan=PlanItem.model_validate(item) if item is not None else None,
        day=NextStartDay.model_validate(next_start_day_state(db, user)),
        series=PlanSeries.model_validate(series_payload(db, user, current)) if current is not None else None,
        series_hint=SeriesHint.model_validate(hint) if hint is not None else None,
    )


@router.post("/plans", response_model=PlanSaveResponse)
def save_plan(
    body: PlanSaveRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> PlanSaveResponse:
    try:
        plan = upsert_plan(
            db,
            user,
            identity_key=body.key.strip(),
            plan_date=body.plan_date,
            is_public=body.is_public,
            keep_series=body.keep_series,
            replace_key=body.replace_key.strip() if body.replace_key else None,
        )
    except PlanError as exc:
        db.rollback()
        raise _raise(exc) from exc
    db.commit()
    context = plan_save_context(db, user, plan)
    return PlanSaveResponse(
        plan=PlanItem.model_validate(context["plan"]),
        displaced=PlanLocation.model_validate(context["displaced"]) if context["displaced"] is not None else None,
        series_sleeping=context["series_sleeping"],
    )


@router.delete(
    "/plans/{plan_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={status.HTTP_409_CONFLICT: {"model": RestConflictResponse}},
)
def remove_plan(
    plan_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    rest: Annotated[bool, Query()] = False,
) -> Response:
    """Удалить план. rest=true — и отметить «Не бегаю» на его дату в той же
    транзакции («Убрать поездку» → «Не бегаю в этот день» вместо «Вернуть
    постоянную локацию»). На дате ещё есть планы (особый день) — 409, как у
    «Не бегаю», и ничего не удалено."""
    try:
        delete_plan(db, user, plan_id, rest=rest)
    except RestConflict as exc:
        plans = [PlanItem.model_validate(item) for item in plan_items(db, user, exc.plans)]
        db.rollback()
        payload = RestConflictResponse(detail=exc.message, plans=plans)
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=jsonable_encoder(payload))
    except PlanError as exc:
        db.rollback()
        raise _raise(exc) from exc
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/rest/{plan_date}",
    response_model=RestResponse,
    responses={status.HTTP_409_CONFLICT: {"model": RestConflictResponse}},
)
def save_rest(
    plan_date: date,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    body: RestRequest | None = None,
) -> RestResponse | JSONResponse:
    """«Не бегаю» на день старта. На день с планами пробежки — 409: detail —
    готовый вопрос окна подтверждения, plans — что уйдёт; повтор с
    replace=true удаляет эти планы и ставит отметку. is_public — видна ли
    отметка в календаре профиля; на стоящей отметке — правка публичности."""
    try:
        row = set_rest(
            db,
            user,
            plan_date,
            replace=bool(body and body.replace),
            is_public=body.is_public if body is not None else None,
        )
    except RestConflict as exc:
        plans = [PlanItem.model_validate(item) for item in plan_items(db, user, exc.plans)]
        db.rollback()
        payload = RestConflictResponse(detail=exc.message, plans=plans)
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=jsonable_encoder(payload))
    except PlanError as exc:
        db.rollback()
        raise _raise(exc) from exc
    db.commit()
    return RestResponse(plan=PlanItem.model_validate(plan_item(db, user, row)))


@router.delete("/rest/{plan_date}", status_code=status.HTTP_204_NO_CONTENT)
def remove_rest(
    plan_date: date,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Снять «Не бегаю»."""
    clear_rest(db, user, plan_date)
    db.commit()


# ---------------------------------------------------------------- постоянный план


@router.get("/series", response_model=MySeriesResponse)
def get_my_series(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> MySeriesResponse:
    """Постоянный план человека и подсказка «Вы пробежали здесь 9 из 10…»
    (подсказки нет, пока план покрывает сегодня или будущее)."""
    current = active_series(db, user.id)
    hint = series_hint(db, user)
    return MySeriesResponse(
        series=PlanSeries.model_validate(series_payload(db, user, current)) if current is not None else None,
        hint=SeriesHint.model_validate(hint) if hint is not None else None,
    )


@router.put(
    "/series",
    response_model=SeriesSaveResponse,
    responses={status.HTTP_409_CONFLICT: {"model": SeriesConflictResponse}},
)
def put_series(
    body: SeriesSaveRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> SeriesSaveResponse | JSONResponse:
    """«Планирую каждую субботу». Постоянный план на другой локации — 409:
    detail — готовый вопрос, series — нынешний план; повтор с replace=true
    заменяет его (прошлые субботы остаются в истории). is_public — один выбор
    на весь план; правка публичности — тот же запрос с прежними key и сроком."""
    try:
        series, replaced, failed = save_series(
            db,
            user,
            identity_key=body.key.strip(),
            starts_on=body.starts_on,
            until=body.until,
            skip=body.skip,
            unskip=body.unskip,
            replace=body.replace,
            replace_first=body.replace_first,
            is_public=body.is_public,
        )
    except SeriesConflict as exc:
        current = PlanSeries.model_validate(series_payload(db, user, exc.series))
        db.rollback()
        payload = SeriesConflictResponse(detail=exc.message, series=current)
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=jsonable_encoder(payload))
    except PlanError as exc:
        db.rollback()
        raise _raise(exc) from exc
    db.commit()
    return SeriesSaveResponse(
        series=PlanSeries.model_validate(series_payload(db, user, series)),
        replaced=PlanLocation.model_validate(replaced) if replaced is not None else None,
        skipped_failed=failed,
    )


@router.delete("/series", status_code=status.HTTP_204_NO_CONTENT)
def delete_series(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    """«Остановить» и «Отменить» после одного нажатия: будущие субботы уходят,
    прошлые остаются; план без прошлого удаляется. Плана нет — тоже 204."""
    stop_series(db, user)
    db.commit()


@router.post("/series/resume", response_model=SeriesResumeResponse)
def post_series_resume(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> SeriesResumeResponse:
    """«Продолжить» уснувший план. Плана нет — 404; не спит — без изменений."""
    try:
        series = resume_series(db, user)
    except PlanError as exc:
        db.rollback()
        raise _raise(exc) from exc
    db.commit()
    return SeriesResumeResponse(series=PlanSeries.model_validate(series_payload(db, user, series)))


@router.get("/series/preview", response_model=SeriesPreviewResponse)
def get_series_preview(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    key: Annotated[str, Query(min_length=1, max_length=255)],
    starts_on: Annotated[date | None, Query()] = None,
    until: Annotated[date | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=SERIES_PREVIEW_MAX)] = SERIES_PREVIEW_DEFAULT,
) -> SeriesPreviewResponse:
    """Что встанет в постоянный план: ближайшие даты с состоянием и сколько
    стартов в сроке — для окна «каждую субботу» до сохранения."""
    try:
        payload = series_preview(db, user, identity_key=key, starts_on=starts_on, until=until, limit=limit)
    except PlanError as exc:
        raise _raise(exc) from exc
    return SeriesPreviewResponse.model_validate(payload)


@router.post("/series/hint/snooze", status_code=status.HTTP_204_NO_CONTENT)
def post_series_hint_snooze(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    """«Не сейчас» у подсказки — 30 дней на всех устройствах."""
    snooze_series_hint(db, user)
    db.commit()
