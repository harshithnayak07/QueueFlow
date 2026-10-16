"""S5: virtual-queue / token service (pure functions, no DB imports).

Owns the token lifecycle rules so route handlers stay thin:

- Status transitions are forward-only. ``served`` / ``cancelled`` are
  terminal; a token can never leave them. Same-status updates are a
  no-op success (idempotent), never an error.
- Per-token people-ahead: the confirmed physical remainder for active
  tokens (waiting/called). Virtual tokens are access to the detected
  physical capacity, not extra people, so no earlier-token count is
  added. Served / cancelled / expired tokens occupy no queue position,
  and such a token itself has nobody ahead.
- ``recommended_return_at`` is ``now + estimated_wait``; None when the
  wait is not computable. Callers pass None through as "unavailable".

Wait math itself lives in waiting_time_service; this module only
decides WHO counts as ahead.
"""

from datetime import datetime, timedelta, timezone
import math

from app.models.token import (
    TOKEN_STATUS_CALLED,
    TOKEN_STATUS_CANCELLED,
    TOKEN_STATUS_EXPIRED,
    TOKEN_STATUS_SERVED,
    TOKEN_STATUS_WAITING,
)

TOKEN_STATUSES = (
    TOKEN_STATUS_WAITING,
    TOKEN_STATUS_CALLED,
    TOKEN_STATUS_SERVED,
    TOKEN_STATUS_CANCELLED,
    TOKEN_STATUS_EXPIRED,
)

TERMINAL_TOKEN_STATUSES = (
    TOKEN_STATUS_SERVED,
    TOKEN_STATUS_CANCELLED,
    TOKEN_STATUS_EXPIRED,
)

# Post-call validity window (E2, owner-approved): a called token expires
# this many minutes after being called unless served/cancelled first.
TOKEN_VALIDITY_MINUTES_AFTER_CALLED = 30

# Forward-only transitions (S5 assumption: no requeue from called back to
# waiting; the owner cancels and the customer takes a fresh token).
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    TOKEN_STATUS_WAITING: frozenset(
        {TOKEN_STATUS_CALLED, TOKEN_STATUS_SERVED, TOKEN_STATUS_CANCELLED}
    ),
    TOKEN_STATUS_CALLED: frozenset(
        {TOKEN_STATUS_SERVED, TOKEN_STATUS_CANCELLED}
    ),
    TOKEN_STATUS_SERVED: frozenset(),
    TOKEN_STATUS_CANCELLED: frozenset(),
    # "expired" is system-only (lazy sweep): owners can never move a token
    # to/from it manually, and it can never leave (terminal).
    TOKEN_STATUS_EXPIRED: frozenset(),
}


def normalize_customer_contact(raw: str) -> str:
    """Canonical contact form so one phone number means one token.

    - Emails (contain '@'): stripped + lowercased, format untouched.
    - Phones: keep an optional leading '+', drop every other non-digit.
      Indian 10-digit mobiles starting 6-9 (optionally with trunk-prefix
      0 or country code 91) canonicalize to +91XXXXXXXXXX, matching the
      customer join UI which always sends +91 numbers.
    - Anything else must hold 7-15 digits (E.164 range).

    Raises ValueError for blank/unusable input. Idempotent:
    normalizing twice gives the same result (required for backfills).
    """
    v = (raw or "").strip()
    if not v:
        raise ValueError("customer_contact must not be blank")
    if "@" in v:
        return v.lower()
    digits = "".join(ch for ch in v if ch.isdigit())
    if len(digits) == 10 and digits[0] in "6789":
        digits = "91" + digits
    elif len(digits) == 11 and digits[0] == "0" and digits[1] in "6789":
        digits = "91" + digits[1:]
    if not 7 <= len(digits) <= 15:
        raise ValueError(
            "customer_contact must be a phone number (7-15 digits) "
            "or an email address"
        )
    return "+" + digits


def validity_for_estimate(
    estimated_wait_minutes: float | None,
    fallback_minutes: int,
) -> int:
    """Per-token validity duration (whole minutes) from the estimated wait.

    The validity shown to the customer IS the estimated wait: a 5-minute
    estimate means a 5-minute validity, a 2-minute estimate means 2.
    Minimum 1 minute for any positive/computed wait (a zero-wait token is
    called almost immediately and its in-service window — not expiry —
    governs it). None (uncomputable, e.g. zero counters) falls back to
    the owner's session setting. No separate hardcoded duration exists.
    """
    if estimated_wait_minutes is None:
        return fallback_minutes
    return max(1, math.ceil(estimated_wait_minutes))


def validate_status_transition(current: str, new: str) -> bool:
    """Validate an owner-requested status change.

    Returns True when the status actually changes, False when it is a
    same-status no-op. Raises ValueError describing the problem for
    anything else (unknown status or illegal transition); routes map
    that to HTTP 409.
    """
    if new not in TOKEN_STATUSES:
        raise ValueError(f"Unknown token status: {new!r}.")
    if current not in TOKEN_STATUSES:
        raise ValueError(f"Unknown current token status: {current!r}.")
    if current == new:
        return False
    if new not in ALLOWED_TRANSITIONS[current]:
        raise ValueError(
            f"Cannot move token from {current!r} to {new!r}. "
            f"{current!r} tokens can only move to "
            f"{sorted(ALLOWED_TRANSITIONS[current]) or 'nowhere (terminal)'}."
        )
    return True


def queue_ahead_estimate(
    ahead_tokens: list[tuple[str, datetime | None]],
    average_service_minutes: float,
    active_counters: int | None,
    now: datetime | None = None,
    own_service_ends_at: datetime | None = None,
) -> tuple[int, float | None]:
    """One authoritative people-ahead + wait rule (dashboard + tracking).

    ahead_tokens: (status, service_ends_at) of the eligible customers
      ahead — every active token for the session view, earlier active
      tokens for one customer's view. Terminal tokens and the tracked
      token itself are never included by callers.
    people-ahead = how many tokens are ahead. wait = remaining service
      time of in-service ones (ends_at still in the future) plus a full
      average_service_minutes for each other one, divided by counters.
    own_service_ends_at: when the tracked token itself is in service,
      its wait is its own remaining time (service completion does not
      divide by counters).
    Returns (people_ahead, estimated_wait_minutes | None). None wait
    when counters are missing/zero — callers render "unavailable".
    """
    if average_service_minutes is None or average_service_minutes < 0:
        raise ValueError("average_service_minutes must be >= 0")
    if active_counters is not None and active_counters < 0:
        raise ValueError("active_counters must be >= 0")
    now = _as_aware(now) or datetime.now(timezone.utc)
    count = len(ahead_tokens)
    if own_service_ends_at is not None:
        own_end = _as_aware(own_service_ends_at)
        if own_end is not None and own_end > now:
            if active_counters is None or active_counters <= 0:
                return count, None
            return count, round(
                (own_end - now).total_seconds() / 60.0, 2
            )
        # Own window elapsed but status not yet reconciled: fall through
        # to ahead-based math (the next reconcile serves it on this read).
    work = 0.0
    for status, ends_at in ahead_tokens:
        end = _as_aware(ends_at)
        if status == TOKEN_STATUS_CALLED and end is not None and end > now:
            work += max(0.0, (end - now).total_seconds() / 60.0)
        else:
            work += average_service_minutes
    # NOTE: work already sums full durations, so divide by counters
    # directly — calling estimate_wait_minutes(work, avg, …) here would
    # multiply by the average a second time.
    if active_counters is None or active_counters == 0:
        return count, None
    return count, round(work / active_counters, 2)


def recommended_return_at(
    estimated_wait_minutes: float | None,
    now: datetime | None = None,
) -> datetime | None:
    """Return-time hint = now + estimated wait; None when not computable."""
    if estimated_wait_minutes is None:
        return None
    if estimated_wait_minutes < 0:
        raise ValueError("estimated_wait_minutes must be >= 0")
    now = now or datetime.now(timezone.utc)
    return now + timedelta(minutes=estimated_wait_minutes)


def token_expiry_time(
    called_at: datetime | None,
    validity_minutes: int = TOKEN_VALIDITY_MINUTES_AFTER_CALLED,
) -> datetime | None:
    """When a called token's validity window ends (None if never called)."""
    if called_at is None:
        return None
    return called_at + timedelta(minutes=validity_minutes)


def is_token_expired(
    status: str,
    expires_at: datetime | None,
    now: datetime | None = None,
) -> bool:
    """True when a called token's window has passed (E2)."""
    if status != TOKEN_STATUS_CALLED or expires_at is None:
        return False
    now = now or datetime.now(timezone.utc)
    if expires_at.tzinfo is None:
        # SQLite returns naive datetimes; stored values are UTC.
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now >= expires_at


def _as_aware(dt):
    """SQLite returns naive datetimes; stored values are UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def expire_overdue_tokens(db_session, session_id: int, now=None) -> int:
    """Flip overdue called tokens of one session to "expired" (lazy sweep).

    Called from read paths (tracking, session status, token list) so expiry
    takes effect even when the customer never refreshes, as long as anyone
    (owner dashboard polling, another customer) reads queue state. Commits
    inside GETs deliberately — documented side effect, covered by tests.
    Lives here (not in the pure section above) and imports models lazily
    to keep module import free of DB dependencies.
    """
    from app.models.token import Token

    now = now or datetime.now(timezone.utc)
    overdue = (
        db_session.query(Token)
        .filter(
            Token.session_id == session_id,
            Token.status == TOKEN_STATUS_CALLED,
        )
        .all()
    )
    flipped = 0
    for token in overdue:
        # A token with an unelapsed service window is in service, not
        # abandoned — automatic progression (not expiry) owns it. This
        # matters whenever average service time exceeds the 30-minute
        # validity window; without the guard, long services would be
        # wrongly expired mid-service.
        if token.service_ends_at is not None:
            ends = _as_aware(token.service_ends_at)
            check = _as_aware(now)
            if ends is not None and check is not None and ends > check:
                continue
        # Prefer the stored window; fall back to called_at + default.
        window_end = token.expires_at or token_expiry_time(token.called_at)
        if window_end is None:
            continue
        if window_end.tzinfo is None:
            window_end = window_end.replace(tzinfo=timezone.utc)
        check_now = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
        if check_now >= window_end:
            token.status = TOKEN_STATUS_EXPIRED
            flipped += 1
    if flipped:
        db_session.commit()
    return flipped


def progress_session(db_session, queue_session, now=None) -> int:
    """Advance backend-authoritative time-based queue progression.

    The serving engine — no frontend involved, no timers in memory (all
    state lives in token.service_started_at/service_ends_at rows, so
    restarts simply resume from the database):
    - Up to active_counters tokens serve concurrently, each for
      average_service_minutes from its window start.
    - A serving token past service_ends_at is persisted as served with
      served_at = scheduled completion (never early), and its slot is
      immediately refilled by the next eligible token chained from the
      freed slot time — a downtime backlog therefore reconciles fully in
      one pass instead of one token per poll.
    - Idle slots (nothing completed) are filled starting now.
    - Fill order: manually-called (windowless) tokens first — an explicit
      owner Call wins over FIFO — then earliest waiting tokens.
    - Served/cancelled/expired rows are never touched; closed sessions and
      zero-counter sessions do not progress; no tokens are ever issued
      here (capacity rules untouched).
    - Commits only when something changed. Returns served-transition count.
    """
    from app.models.token import Token

    now = _as_aware(now) or datetime.now(timezone.utc)
    if queue_session.status == "closed":
        return 0
    counters = queue_session.active_counters or 0
    if counters <= 0:
        return 0
    duration = timedelta(minutes=queue_session.average_service_minutes or 0)

    # Bound: each loop pass serves exactly one token, so waiting+called+1
    # passes always terminate (even with zero service duration).
    remaining = (
        db_session.query(Token)
        .filter(
            Token.session_id == queue_session.id,
            Token.status.in_((TOKEN_STATUS_WAITING, TOKEN_STATUS_CALLED)),
        )
        .count()
    )
    served_flips = 0
    changed = False

    def serving():
        return (
            db_session.query(Token)
            .filter(
                Token.session_id == queue_session.id,
                Token.status == TOKEN_STATUS_CALLED,
                Token.service_ends_at.isnot(None),
            )
            .order_by(Token.service_ends_at.asc(), Token.token_number.asc())
            .all()
        )

    def next_eligible():
        manual = (
            db_session.query(Token)
            .filter(
                Token.session_id == queue_session.id,
                Token.status == TOKEN_STATUS_CALLED,
                Token.service_ends_at.is_(None),
            )
            .order_by(Token.token_number.asc())
            .first()
        )
        if manual is not None:
            return manual
        return (
            db_session.query(Token)
            .filter(
                Token.session_id == queue_session.id,
                Token.status == TOKEN_STATUS_WAITING,
            )
            .order_by(Token.token_number.asc())
            .first()
        )

    def start_service(token, start):
        token.status = TOKEN_STATUS_CALLED
        if token.called_at is None:
            token.called_at = start
        token.service_started_at = start
        token.service_ends_at = start + duration

    for _ in range(remaining + 1):
        overdue = [t for t in serving() if _as_aware(t.service_ends_at) <= now]
        if not overdue:
            break
        current = overdue[0]
        completed_at = _as_aware(current.service_ends_at)
        current.status = TOKEN_STATUS_SERVED
        current.served_at = completed_at
        served_flips += 1
        changed = True
        nxt = next_eligible()
        if nxt is not None:
            start_service(nxt, completed_at)
            changed = True
        # Explicit flush: sessions in this app run with autoflush=False,
        # so without this the next query in the loop would read stale rows
        # (e.g. re-serve the token just served). Flush makes each pass see
        # the previous one; the final commit persists everything.
        db_session.flush()

    current_serving = len(serving())
    # Leftover expiry BEFORE top-up (order matters): overdue calls flip to
    # expired here, so the fill below can never re-arm a stale call with a
    # fresh window and dodge expiry forever. Service completions above
    # already ran, so a finished service always wins over this sweep.
    expire_overdue_tokens(db_session, queue_session.id, now)
    current_serving = len(serving())
    while current_serving < counters:
        nxt = next_eligible()
        if nxt is None:
            break
        start_service(nxt, now)
        changed = True
        current_serving += 1
        db_session.flush()

    if changed:
        db_session.commit()
    return served_flips
