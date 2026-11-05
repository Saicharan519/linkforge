# LinkForge — Traceability (PRD requirement → task → tests)

Test names are the PLANNED names; the implementing task may adjust wording but must keep one test per row's intent. A requirement is "done" only when its test exists and passes against real Postgres/Redis. "D#" = `docs/DECISIONS.md`. Phase/task ids = `docs/TASKS.md`.

## F1 Registration & sessions [M]
| Requirement | Task | Tests |
|---|---|---|
| Email trimmed, lowercased, ≤254, valid, unique (case-insensitive) | P1.2, P2.4 | `test_unique_email_ci`, `test_register_email_normalised`, `test_register_email_too_long_422`, `test_register_duplicate_409` |
| Password 10–128 chars, argon2id with library defaults | P2.4 | `test_register_password_length_422`, `test_argon2_defaults`, `test_hash_is_argon2id` |
| Hash/verify behind Semaphore(2), off the event loop | P2.4 | `test_argon2_semaphore_limits_concurrency` |
| Login creates Redis session, 7-day sliding TTL | P2.4 | `test_login_creates_session`, `test_session_ttl_sliding_refresh_once_per_minute` |
| Cookie flags: HttpOnly, SameSite=Lax, host-only, Secure by env | P2.4 | `test_login_cookie_flags_dev`, `test_login_cookie_flags_prod` |
| Session ID rotates on login | P2.4 | `test_session_rotates_on_login` |
| Logout destroys session server-side | P2.4 | `test_logout_destroys_session` |
| Wrong password and unknown email → same 401 | P2.4 | `test_login_wrong_password_and_unknown_email_identical` |
| Inactive user cannot log in (D34) | P2.4 | `test_inactive_user_login_401` |
| Malformed/expired/revoked session → 401 and cookies cleared | P2.4 | `test_malformed_session_401_clears_cookies`, `test_expired_session_401`, `test_revoked_session_401` |
| Login limit 5/15 min per IP+email → 429, fails CLOSED if Redis is down | P2.3, P2.4 | `test_login_rate_limit_429`, `test_login_limiter_fails_closed_when_redis_down` |
| CSRF token (double-submit) required on unsafe cookie-auth requests | P2.5 | `test_csrf_missing_token_403`, `test_csrf_mismatch_403` |
| Origin must be allowed; absent Origin needs Sec-Fetch-Site same-origin/none | P2.5 | `test_origin_not_allowed_403`, `test_origin_absent_sec_fetch_site_fallback` |
| Origin check also on register/login (D25) | P2.5 | `test_login_cross_origin_403`, `test_register_cross_origin_403` |
| API-key requests skip CSRF; X-API-Key precedence over cookie (D26) | P2.5, P2.7 | `test_api_key_skips_csrf`, `test_invalid_api_key_401_even_with_valid_cookie` |
| Change password revokes OTHER sessions, not current, not API keys | P2.5 | `test_change_password_revokes_other_sessions`, `test_change_password_keeps_current_session`, `test_change_password_keeps_api_keys` |

## F2 Create & manage links [M]
| Requirement | Task | Tests |
|---|---|---|
| Destination: http/https only, ≤2048, valid host, not our own short host, no `javascript:`/`data:` | P1.3, P1.4 | table-driven `test_url_validation` |
| Alias 3–32 `[A-Za-z0-9_-]`, case-sensitive, stored as typed | P1.3 | `test_alias_rules`, `test_alias_case_preserved` |
| Reserved words rejected case-insensitively → 422 | P1.3 | `test_reserved_aliases`, `test_reserved_alias_case_insensitive` |
| No alias → random 7-char base62; collisions retried; reserved results regenerated (D33) | P1.3 | `test_random_code_shape`, `test_collision_retry`, `test_random_code_skips_reserved` |
| Duplicate alias → 409 | P1.4 | `test_duplicate_alias_409` |
| `expires_at` must be in the future (create and PATCH, D34) | P1.4 | `test_expires_at_past_422`, `test_patch_expires_at_past_422` |
| Status = disabled / expired / active with the stated precedence | P1.4 | `test_status_active`, `test_status_expired`, `test_status_disabled_beats_expired` |
| List: search q (code/title/URL), status filter, newest first | P1.4 | `test_list_search`, `test_list_status_filter`, `test_list_newest_first` |
| Pagination: page ≥1, page_size 1–100 (default 20), invalid → 422 | P1.4 | `test_page_zero_422`, `test_page_size_0_and_101_422`, `test_page_size_default_20` |
| PATCH omitted = unchanged; explicit null clears title/expires_at; null destination/is_active → 422 | P1.4 | `test_patch_omitted_unchanged`, `test_patch_null_clears_title`, `test_patch_null_clears_expires_at`, `test_patch_null_destination_422`, `test_patch_null_is_active_422` |
| `updated_at` bumped on every PATCH (D34) | P1.4 | `test_patch_updates_updated_at` |
| DELETE is permanent and cascades click data | P1.4 | `test_delete_link_cascades_events` |
| `short_url` from server-side `SHORT_URL_BASE`; `total_clicks` via one grouped query, no N+1 (D17) | P1.4, P3.2 | `test_short_url_uses_base`, `test_total_clicks_excludes_bots`, `test_list_total_clicks_single_query` |
| Cross-user read/modify/delete → 404; every links query scoped by user_id | P1.4, P2.6 | `test_cross_user_get_404`, `test_cross_user_patch_404`, `test_cross_user_delete_404`, `test_cross_user_analytics_404` |
| Edit/delete visible on next redirect; stale race bounded by TTL | P2.2 | `test_edit_then_redirect_immediate`, `test_delete_then_redirect_404`, `test_cache_race_stale_entry_expires_within_ttl` |

## F3 Redirect [M]
| Requirement | Task | Tests |
|---|---|---|
| 302 (never 301) | P1.5 | `test_redirect_302` |
| Unknown → 404, disabled → 404 (even if expired), expired → 410 | P1.5 | `test_unknown_404`, `test_disabled_404_even_if_expired`, `test_expired_410` |
| Postgres down on cache miss → 503; cache hit still served | P1.5, P2.2 | `test_db_down_cache_miss_503`, `test_db_down_cache_hit_still_302` |
| Cache hit / miss / negative; TTL ≤300 s (+jitter) and ≤ link expiry | P2.2 | `test_cache_hit`, `test_cache_miss_populates`, `test_negative_cache_60s`, `test_cache_ttl_capped_by_expiry` |
| Expired (410) not cached (D32) | P2.2 | `test_expired_not_cached` |
| Redis down → still 302 via Postgres, every Redis call bounded to 100 ms | P2.1, P2.2 | `test_redis_down_redirect_still_302`, `test_redis_timeout_helper_returns_fallback` |
| Redirect limiter 120/min per IP → 429; fails OPEN | P2.3 | `test_redirect_rate_limit_429`, `test_redirect_limiter_fails_open_when_redis_down` |
| Client IP via trusted-proxy rule; spoofed X-Forwarded-For ignored | P2.3 | `test_client_ip_trusted_proxy_count`, `test_spoofed_xff_ignored` |
| HEAD: same status/Location, no body, never a click (D12) | P1.5, P3.1 | `test_head_same_status`, `test_head_not_counted_as_click` |
| Public host serves ONLY GET/HEAD /{code}; /api, /docs, /healthz → 404 | P2.8 | `test_host_guard_only_redirect`, `test_host_guard_blocks_api_and_health` |
| Redirect route registered last (cannot shadow /api, /healthz) | P1.5 | `test_routes_not_shadowed_by_redirect` |
| p95 target < 50 ms is REPORTED, not asserted | P8 | k6 results in `docs/RESULTS.md` |

## F4 Click analytics [M]
| Requirement | Task | Tests |
|---|---|---|
| Redirect enqueues an event; queue failure still redirects and counts the drop | P3.1 | `test_xadd_failure_still_302_and_counts_drop`, `test_event_fields` |
| Captured fields: time, hashed IP (HMAC), UA ≤512, referrer host only, device, browser, OS, is_bot | P3.1, P3.4 | `test_ip_hash_is_hmac_not_raw`, `test_referrer_host_only`, `test_ua_parsing_samples` |
| Bots stored with is_bot=true, excluded from counts and charts | P3.4, P3.5 | `test_bot_detection_samples`, `test_bots_excluded_from_rollups_and_charts` |
| Replaying the same event never double counts | P3.2 | `test_duplicate_event_one_row`, `test_replay_twice_one_row_plus_one_count` |
| Commit then crash before XACK → redelivery → still one row, +1 count (D9) | P3.3 | `test_crash_after_commit_before_ack_no_double_count` |
| Orphan (deleted link) in a batch does not block others | P3.2 | `test_batch_with_orphan_others_stored` |
| Batch failure → per-event fallback; FK race treated as orphan | P3.3 | `test_batch_failure_per_event_fallback`, `test_fk_violation_acked_as_orphan` |
| Ack strictly after commit | P3.2 | `test_ack_only_after_commit` |
| Pending reclaim (XAUTOCLAIM) of a crashed consumer | P3.3 | `test_pending_reclaim_after_idle` |
| Poison → dead stream (extended XPENDING counts); ack only after dead XADD succeeds | P3.3 | `test_poison_goes_to_dead_stream`, `test_dead_xadd_failure_stays_pending` |
| Consumer group at `0`; NOGROUP recovery; events before group creation processed (D7) | P3.3 | `test_group_created_at_zero_processes_backlog`, `test_nogroup_recovery_after_redis_wipe` |
| Graceful SIGTERM: finish batch, ack, exit 0 | P3.3 | `test_sigterm_finishes_batch` |
| Worker stopped → redirects still work; backlog drains after restart | P3.6, P8b | `test_redirect_works_with_worker_down` (+ drill in `RESULTS.md`) |
| Range 7d/30d/90d = last N UTC days incl. today, zero-filled, UTC `YYYY-MM-DD` labels | P3.5 | `test_range_boundaries_partial_today`, `test_series_zero_filled`, `test_day_labels_utc_strings` |
| Link analytics: total, series, referrers, devices, browsers | P3.5 | `test_link_analytics_shape` |
| Overview: total links, total clicks, series, top 5 links | P3.5 | `test_overview_shape_top5` |
| Analytics visible ≈10 s after a click | P3.6 | `test_click_to_analytics_latency` (soft, ≤10 s) |
| Dashboard footer "Days are UTC. Counts may undercount during outages." | P4.4 | `test_analytics_footer_text` (frontend) |

## F5 Dashboard UI [M]
| Requirement | Task | Tests |
|---|---|---|
| Screens: Landing, Register, Login, Dashboard, Links, Link analytics, Settings (API keys, change password) | P4.2–P4.5 | RTL render tests per page |
| Loading, empty, error states on every data view | P4.6 | `test_<view>_loading_empty_error` |
| Works at 1280 px and 390 px | P4.6 | manual + screenshots |
| Protected routes redirect to login | P4.1 | `test_protected_route_redirects_to_login` |
| 401 from API clears user cache and redirects (except /auth/me at boot) | P4.1 | `test_401_redirects_to_login` |
| Daily labels rendered as UTC strings exactly as returned | P4.4 | `test_chart_uses_day_strings_as_is` |
| Copy button uses API `short_url`; forms show 422 field errors; CSRF header sent | P4.3 | `test_copy_uses_short_url`, `test_form_shows_422_details`, `test_mutations_send_csrf_header` |

## F6 API keys [M]
| Requirement | Task | Tests |
|---|---|---|
| Create named key; plaintext shown once; sha256 + prefix stored | P2.7 | `test_create_key_returns_plaintext_once`, `test_only_hash_and_prefix_stored` |
| List shows prefix, name, created, last used (debounced ≤1 write/min) | P2.7 | `test_list_keys_no_secret`, `test_last_used_debounced` |
| Revoked key → 401 immediately | P2.7 | `test_revoked_key_401` |
| Key authenticates links + analytics via X-API-Key | P2.7 | `test_api_key_auth_links_and_analytics` |
| Create-link limit 30/min per USER, shared by session and keys; fails closed (D13) | P2.7 | `test_create_limit_shared_session_and_keys`, `test_create_limiter_fails_closed` |

## F7 Operability [M]
| Requirement | Task | Tests |
|---|---|---|
| `/healthz` liveness; `/readyz` Postgres required, Redis down = degraded but ready (D31) | P1.1 | `test_app_boots`, `test_readyz_fails_when_db_unreachable`, `test_readyz_degraded_when_redis_down` |
| All Prometheus metrics (ARCH §7) on :9101 (API) and :9100 (worker); never on the public port | P3.6 | `test_metrics_endpoint_lists_all_names`, `test_metrics_not_served_on_public_port` |
| Route label is the template (`/{code}`), not the raw path | P3.6 | `test_metrics_route_label_is_template` |
| Structured JSON logs with request ID; no PII/secrets | P3.6 | `test_json_log_fields`, `test_logs_contain_no_raw_ip_or_secrets` |
| OpenAPI docs only when `ENABLE_DOCS=true` | P1.1 | `test_docs_disabled_by_default`, `test_docs_enabled_flag` |

## F8 Data retention [C, optional]
| Requirement | Task | Tests |
|---|---|---|
| Job deletes raw click_events older than 90 days; rollups kept | P11.7 | `test_retention_deletes_old_events_keeps_rollups` |

## Non-functional and cross-cutting
| Requirement | Task | Evidence |
|---|---|---|
| Parameterized queries only; no secrets in repo/images/logs | P1, P1.7, P6 | gitleaks job, `test_logs_contain_no_raw_ip_or_secrets` |
| Migrations backward compatible (expand/contract); empty-DB upgrade works | P1.2 | `test_upgrade_on_empty_db`, review checklist in PR template |
| Constraints enforced: unique email, event_id, FK cascade | P1.2 | `test_unique_email_ci`, `test_event_id_unique`, `test_fk_cascade_delete` |
| Ruff clean, tsc strict, tests on real Postgres/Redis in CI, ≥80% backend service coverage | P1.7, P4.7 | CI badge + coverage report |
| Dependency + image scanning gated in CI | P1.7, P6.1 | green Trivy job; Trivy-fails-on-vulnerable-base demo |
| Non-root containers, security headers on the SPA | P1S, P5 | `docker compose exec api id`; header check on :8080 |
| DB TLS verify-full on RDS (D14) | P9 | connection test on EKS |
| Degradation matrix ARCH §8 demonstrated | P8b, P8, P10 | drills recorded in `RESULTS.md` / `VERIFICATION.md` |
| Cost: destroy verified | P10 | console checklist, Cost Explorer next day |

## Definition of done (PRD §7)
| Item | Phase |
|---|---|
| 1. [M] features work via `docker compose up` and on kind via Argo CD | P5, P7D |
| 2. CI green on main; scanned image to ECR (SHA tag); gitops repo auto-updated | P1S, P6 |
| 3. Deployed once on EKS from a clean `terraform apply`, load tested, drills done, destroyed and verified | P9, P10 |
| 4. README with diagram, screenshots, real k6 numbers, demo video, "what breaks when Redis dies" | P11 |
