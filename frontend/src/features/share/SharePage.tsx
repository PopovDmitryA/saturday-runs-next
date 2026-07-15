import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { RequireAuth } from "../../components/RequireAuth";
import { ON_THIS_DAY_SHARE_KEY } from "../../components/OnThisDayCard";
import {
  getCurrentUser,
  getDashboard,
  listRuns,
  type DashboardResponse,
  type OnThisDayRun,
  type RunItem,
  type User,
} from "../../lib/api";
import { formatDateLong, pluralFormRu } from "../../lib/format";
import {
  getShareBackground,
  isCustomBackgroundId,
  loadShareBackgroundImage,
  preloadShareBackgrounds,
  SHARE_BACKGROUNDS,
  type ShareBackgroundId,
} from "./shareBackgrounds";
import { ShareBackgroundCrop } from "./ShareBackgroundCrop";
import {
  cropImageToFormat,
  defaultCropAdjust,
  imageElementFromCanvas,
  loadImageFromFile,
  revokeImageObjectUrl,
} from "./shareCustomBackground";
import {
  RUN_DEFAULT_FIELDS,
  RUN_SHARE_FIELDS,
  SHARE_FIELDS,
  SHARE_FORMATS,
  SHARE_PERIODS,
  SHARE_PRESETS,
  SHARE_TEXT_COLORS,
  type RunFieldId,
  type ShareFieldId,
  type ShareFormatId,
  type SharePeriodId,
  type SharePresetId,
  type ShareTextColor,
  getShareFormat,
  runPrimaryFieldId,
  trimFieldsForFormat,
} from "./shareConfig";
import {
  buildRunMetricRow,
  buildShareMetrics,
  defaultPrimaryFieldId,
  getLastRun,
  layoutMetricZones,
  shareSummaryLine,
  shareUserDisplayName,
  type ShareMetricRow,
} from "./shareMetrics";
import { downloadShareBlob, renderShareCard, shareCardToBlob } from "./shareCardRenderer";
import { ShareGallery } from "./ShareGallery";
import { MILESTONE_SHARE_KEY, type MilestoneSharePayload } from "../history/milestoneShare";

type ShareMode = "gallery" | "wizard";

type WizardStep = "format" | "background" | "period" | "preset" | "fields" | "preview";

type WizardStepDef = {
  step: WizardStep;
  label: string;
};

const CURRENT_YEAR = new Date().getFullYear();

const ANNIVERSARY_YEAR_FORMS = ["год", "года", "лет"] as const;

// «год назад» / «2 года назад» — для яркой плашки-акцента на сториз.
function anniversaryYearsPhrase(years: number): string {
  if (years <= 0) {
    return "сегодня";
  }
  if (years === 1) {
    return "год назад";
  }
  return `${years} ${pluralFormRu(years, ANNIVERSARY_YEAR_FORMS)} назад`;
}

// Историческая пробежка из карточки «В этот день» приходит как OnThisDayRun.
// Приводим её к RunItem, чтобы переиспользовать однопробежечный рендер (last_run).
function onThisDayRunToRunItem(run: OnThisDayRun): RunItem {
  return {
    platform_code: run.platform_code,
    event_date: run.event_date,
    event_number: null,
    location_name: run.location_name,
    location_city: run.location_city,
    location_country: null,
    position: run.position,
    finish_time_display: run.finish_time_display,
    finish_time_sec: run.finish_time_sec,
    pace_display: null,
    pace_sec_per_km: null,
    age_category: null,
    is_pr: run.is_pr,
    is_global_pr: false,
    is_location_pr: false,
    is_crosslinked: false,
    is_first_run: false,
    is_first_run_at_location: false,
    club_name: null,
    achievement_labels: [],
    status: null,
    is_test_event: false,
    event_url: run.event_url,
  };
}

function ShareContent() {
  const [mode, setMode] = useState<ShareMode>("gallery");
  const [user, setUser] = useState<User | null>(null);
  const [dashboard, setDashboard] = useState<DashboardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [step, setStep] = useState<WizardStep>("format");
  const [formatId, setFormatId] = useState<ShareFormatId>("story");
  const [backgroundId, setBackgroundId] = useState<ShareBackgroundId>("park_alley");
  const [periodId, setPeriodId] = useState<SharePeriodId>("all_time");
  const [presetId, setPresetId] = useState<SharePresetId | null>(null);
  const [selectedFields, setSelectedFields] = useState<ShareFieldId[]>([]);
  // Поля для постера одной пробежки (last_run / годовщина) — настраиваемо.
  const [runFields, setRunFields] = useState<RunFieldId[]>(RUN_DEFAULT_FIELDS);
  const [runs, setRuns] = useState<RunItem[]>([]);
  // Конкретная историческая пробежка (из карточки «В этот день»); если задана —
  // однопробежечный режим показывает её вместо самой последней пробежки.
  const [targetRun, setTargetRun] = useState<RunItem | null>(null);
  // Сколько лет назад была targetRun — для подписи «В этот день N лет назад».
  const [targetRunYears, setTargetRunYears] = useState<number | null>(null);
  // Текст акцент-плашки вехи из «Моей истории» (?story=milestone);
  // если задан — перекрывает плашку годовщины.
  const [targetAccentLabel, setTargetAccentLabel] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState<string | null>(null);
  const [renderError, setRenderError] = useState<string | null>(null);
  const [stockBackgroundImage, setStockBackgroundImage] = useState<HTMLImageElement | null>(null);
  const [customSourceImage, setCustomSourceImage] = useState<HTMLImageElement | null>(null);
  const [customBackgroundImage, setCustomBackgroundImage] = useState<HTMLImageElement | null>(null);
  const [customDragOver, setCustomDragOver] = useState(false);
  const [textColor, setTextColor] = useState<ShareTextColor>("white");
  const previewCanvasRef = useRef<HTMLCanvasElement>(null);
  const exportCanvasRef = useRef<HTMLCanvasElement>(null);
  const customFileInputRef = useRef<HTMLInputElement>(null);

  const format = useMemo(() => getShareFormat(formatId), [formatId]);
  const thumbAspectRatio = `${format.width} / ${format.height}`;
  const background = useMemo(() => getShareBackground(backgroundId), [backgroundId]);
  const availableTextColors = useMemo(() => {
    const allowed = background.allowedTextColors;
    if (!allowed || allowed.length === 0) {
      return SHARE_TEXT_COLORS;
    }
    return SHARE_TEXT_COLORS.filter((item) => allowed.includes(item.id));
  }, [background]);
  const isCustomBackground = isCustomBackgroundId(backgroundId);
  const backgroundImage = isCustomBackground ? customBackgroundImage : stockBackgroundImage;

  const clearCustomBackground = useCallback(() => {
    setCustomSourceImage((prev) => {
      revokeImageObjectUrl(prev);
      return null;
    });
    setCustomBackgroundImage(null);
  }, []);

  const handleCustomFile = useCallback(async (file: File) => {
    if (!file.type.startsWith("image/")) {
      setRenderError("Выберите файл изображения (JPG, PNG, WebP)");
      return;
    }
    setRenderError(null);
    try {
      const img = await loadImageFromFile(file);
      setCustomSourceImage((prev) => {
        revokeImageObjectUrl(prev);
        return img;
      });
      setCustomBackgroundImage(null);
      setBackgroundId("custom");
    } catch (err) {
      setRenderError(err instanceof Error ? err.message : "Не удалось загрузить фото");
    }
  }, []);

  const selectStockBackground = (id: ShareBackgroundId) => {
    clearCustomBackground();
    setBackgroundId(id);
  };

  const selectCustomBackground = () => {
    setBackgroundId("custom");
    if (!customSourceImage) {
      customFileInputRef.current?.click();
    }
  };

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [userResponse, dashboardResponse, runsResponse] = await Promise.all([
        getCurrentUser(),
        getDashboard(),
        listRuns(false, 200),
      ]);
      setUser(userResponse);
      setDashboard(dashboardResponse);
      setRuns(runsResponse);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить данные");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    preloadShareBackgrounds();
  }, [load]);

  useEffect(() => {
    return () => {
      clearCustomBackground();
    };
  }, [clearCustomBackground]);

  useEffect(() => {
    if (isCustomBackgroundId(backgroundId)) {
      return;
    }
    const stock = getShareBackground(backgroundId);
    if (!stock.url) {
      return;
    }
    let cancelled = false;
    setRenderError(null);
    void loadShareBackgroundImage(stock)
      .then((img) => {
        if (!cancelled) {
          setStockBackgroundImage(img);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setStockBackgroundImage(null);
          setRenderError(err instanceof Error ? err.message : "Не удалось загрузить фон");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [backgroundId]);

  useEffect(() => {
    setCustomBackgroundImage(null);
  }, [formatId]);

  // Как только выбрано своё фото — сразу готовим кадр по умолчанию (кроп под
  // формат). Раньше это было привязано к шагу «Период», который в режиме одной
  // пробежки пропускается → фон не собирался и на сториз был пустой/размытый.
  useEffect(() => {
    if (!customSourceImage || !isCustomBackground || customBackgroundImage) {
      return;
    }
    let cancelled = false;
    const canvas = cropImageToFormat(
      customSourceImage,
      format.width,
      format.height,
      defaultCropAdjust(),
    );
    void imageElementFromCanvas(canvas).then((img) => {
      if (!cancelled) {
        setCustomBackgroundImage(img);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [customBackgroundImage, customSourceImage, format.height, format.width, isCustomBackground]);

  useEffect(() => {
    if (availableTextColors.some((item) => item.id === textColor)) {
      return;
    }
    setTextColor(availableTextColors[0]?.id ?? "white");
  }, [availableTextColors, backgroundId, textColor]);

  const lastRun = useMemo(() => getLastRun(runs), [runs]);
  // Пробежка для однопробежечного режима (last_run): историческая из «В этот
  // день», если пришли по deep-link, иначе — самая последняя.
  const singleRun = targetRun ?? lastRun;
  // Шеринг одной конкретной пробежки (из «В этот день»): период не нужен,
  // мастер идёт формат → фон → поля → превью.
  const isSingleRunShare = targetRun != null;
  // Постер одной пробежки (последняя или годовщина) — настраиваемые run-поля.
  const isRunCard = periodId === "last_run";

  // Deep-link'и из карточки «В этот день» и «Моей истории»:
  //   ?story=last_run    → мастер с последней пробежкой в главной роли;
  //   ?story=on_this_day → мастер с конкретной исторической пробежкой (её кладёт
  //                        карточка в sessionStorage перед переходом);
  //   ?story=milestone   → мастер с вехой из «Моей истории» (пробежка + текст
  //                        акцент-плашки в sessionStorage).
  const deepLinkAppliedRef = useRef(false);
  useEffect(() => {
    if (deepLinkAppliedRef.current || loading) {
      return;
    }
    const story = new URLSearchParams(window.location.search).get("story");
    if (story === "milestone") {
      let payload: MilestoneSharePayload | null = null;
      try {
        const raw = sessionStorage.getItem(MILESTONE_SHARE_KEY);
        if (raw) {
          payload = JSON.parse(raw) as MilestoneSharePayload;
        }
      } catch {
        payload = null;
      }
      sessionStorage.removeItem(MILESTONE_SHARE_KEY);
      window.history.replaceState(null, "", "/share");
      deepLinkAppliedRef.current = true;
      if (payload?.run && payload.accent_label) {
        setTargetRun(payload.run);
        setTargetAccentLabel(payload.accent_label);
        setMode("wizard");
        setFormatId("story");
        setPeriodId("last_run");
        setPresetId(null);
        setStep("background");
      }
      return;
    }
    if (story === "on_this_day") {
      let picked: RunItem | null = null;
      let pickedYears: number | null = null;
      try {
        const raw = sessionStorage.getItem(ON_THIS_DAY_SHARE_KEY);
        if (raw) {
          const parsed = JSON.parse(raw) as OnThisDayRun;
          picked = onThisDayRunToRunItem(parsed);
          pickedYears = parsed.years_ago;
        }
      } catch {
        picked = null;
      }
      sessionStorage.removeItem(ON_THIS_DAY_SHARE_KEY);
      window.history.replaceState(null, "", "/share");
      deepLinkAppliedRef.current = true;
      if (picked) {
        setTargetRun(picked);
        setTargetRunYears(pickedYears);
        setMode("wizard");
        setFormatId("story");
        setPeriodId("last_run");
        setPresetId(null);
        // Сначала выбор фона, затем превью (по фидбеку — не прятать фон за
        // отдельной кнопкой на превью).
        setStep("background");
      }
      return;
    }
    if (story === "last_run" && lastRun) {
      setMode("wizard");
      setFormatId("story");
      setPeriodId("last_run");
      setPresetId(null);
      setStep("preview");
      deepLinkAppliedRef.current = true;
      window.history.replaceState(null, "", "/share");
    }
  }, [loading, lastRun]);
  const metricsContext = useMemo(
    () => ({
      period: periodId,
      runs,
      currentYear: CURRENT_YEAR,
    }),
    [periodId, runs],
  );

  const activePreset = useMemo(
    () => SHARE_PRESETS.find((item) => item.id === presetId) ?? null,
    [presetId],
  );

  const effectiveFields = useMemo(() => {
    if (periodId === "last_run") {
      return [];
    }
    if (presetId === "custom") {
      return trimFieldsForFormat(selectedFields, format);
    }
    if (!activePreset) {
      return [];
    }
    return trimFieldsForFormat(activePreset.fields, format);
  }, [activePreset, format, periodId, presetId, selectedFields]);

  const effectivePrimaryFieldId = useMemo(() => {
    if (periodId === "last_run") {
      return null; // постер одной пробежки считается отдельным run-путём
    }
    if (presetId === "custom") {
      return defaultPrimaryFieldId(effectiveFields);
    }
    return activePreset?.primaryFieldId ?? defaultPrimaryFieldId(effectiveFields);
  }, [activePreset, effectiveFields, periodId, presetId]);

  const hasLinks = useMemo(() => {
    const links = dashboard?.platform_links ?? [];
    return links.length > 0;
  }, [dashboard]);

  const cardInput = useMemo(() => {
    if (!dashboard?.stats) {
      return null;
    }
    if (periodId === "last_run" && !singleRun) {
      return null;
    }
    // Постер одной пробежки (last_run / годовщина) — из настраиваемых run-полей;
    // агрегатные карточки — из выбранного пресета/полей.
    let metrics: (ShareMetricRow | null)[];
    if (periodId === "last_run" && singleRun) {
      const run = singleRun;
      metrics = layoutMetricZones(runFields, runPrimaryFieldId(runFields), (fieldId) =>
        buildRunMetricRow(run, fieldId),
      );
    } else {
      const metricRows = buildShareMetrics(dashboard.stats, effectiveFields, metricsContext, singleRun);
      const rowById = new Map(metricRows.map((row) => [row.id, row]));
      metrics = layoutMetricZones(effectiveFields, effectivePrimaryFieldId, (fieldId) =>
        rowById.get(fieldId) ?? null,
      );
    }

    // Яркая плашка-акцент: для вехи из «Моей истории» — её название («КЛУБ 50»),
    // для годовщины из «В этот день» — «В ЭТОТ ДЕНЬ · 2 ГОДА НАЗАД»; под именем
    // в обоих случаях дата пробежки, иначе — обычная сводка периода.
    const anniversaryYears =
      periodId === "last_run" && targetRun ? targetRunYears : null;
    const milestoneAccent = periodId === "last_run" && targetRun ? targetAccentLabel : null;
    const summaryLine =
      (milestoneAccent != null || anniversaryYears != null) && singleRun
        ? formatDateLong(singleRun.event_date)
        : shareSummaryLine(metricsContext, singleRun);
    const accentLabel =
      milestoneAccent ??
      (anniversaryYears != null
        ? `В этот день · ${anniversaryYearsPhrase(anniversaryYears)}`
        : null);

    return {
      format,
      displayName: shareUserDisplayName(user),
      metrics,
      backgroundImage,
      textColor,
      summaryLine,
      accentLabel,
    };
  }, [
    backgroundImage,
    dashboard,
    effectiveFields,
    effectivePrimaryFieldId,
    format,
    singleRun,
    metricsContext,
    periodId,
    runFields,
    targetRun,
    targetRunYears,
    targetAccentLabel,
    textColor,
    user,
  ]);

  useEffect(() => {
    if (step !== "preview" || !cardInput || !previewCanvasRef.current) {
      return;
    }
    renderShareCard(previewCanvasRef.current, cardInput);
  }, [cardInput, step]);

  // Возврат к примерам сбрасывает одиночный режим «В этот день», чтобы он не
  // протёк в следующий обычный мастер.
  const backToGallery = () => {
    setMode("gallery");
    setTargetRun(null);
    setTargetRunYears(null);
    setTargetAccentLabel(null);
  };

  const startFreshWizard = () => {
    setTargetRun(null);
    setTargetRunYears(null);
    setTargetAccentLabel(null);
    setRunFields(RUN_DEFAULT_FIELDS);
    setStep("format");
    setMode("wizard");
  };

  const toggleField = (fieldId: ShareFieldId) => {
    setSelectedFields((prev) => {
      if (prev.includes(fieldId)) {
        return prev.filter((id) => id !== fieldId);
      }
      if (prev.length >= format.maxFields) {
        return prev;
      }
      return [...prev, fieldId];
    });
  };

  const toggleRunField = (fieldId: RunFieldId) => {
    setRunFields((prev) => {
      if (prev.includes(fieldId)) {
        // Не даём убрать последнее поле — постер не может быть пустым.
        return prev.length > 1 ? prev.filter((id) => id !== fieldId) : prev;
      }
      if (prev.length >= format.maxFields) {
        return prev;
      }
      return [...prev, fieldId];
    });
  };

  const handleSelectPreset = (id: SharePresetId) => {
    setPresetId(id);
    if (id === "custom") {
      setSelectedFields([]);
      setStep("fields");
      return;
    }
    setStep("preview");
  };

  const handlePeriodContinue = () => {
    if (periodId === "last_run") {
      setPresetId(null);
      setStep("fields"); // постер последней пробежки — настройка полей
      return;
    }
    setStep("preset");
  };

  const periodTitle = useMemo(() => {
    const period = SHARE_PERIODS.find((item) => item.id === periodId);
    if (!period) {
      return "";
    }
    if (period.id === "year") {
      return `За ${CURRENT_YEAR} год`;
    }
    return period.title;
  }, [periodId]);

  const wizardSteps = useMemo((): WizardStepDef[] => {
    // Шеринг одной пробежки из «В этот день»: формат → фон → поля → превью.
    if (isSingleRunShare) {
      return [
        { step: "format", label: "Формат" },
        { step: "background", label: "Фон" },
        { step: "fields", label: "Поля" },
        { step: "preview", label: "Превью" },
      ];
    }
    const steps: WizardStepDef[] = [
      { step: "format", label: "Формат" },
      { step: "background", label: "Фон" },
      { step: "period", label: "Период" },
    ];
    if (periodId === "last_run") {
      // Постер последней пробежки — тоже настраиваемые поля.
      steps.push({ step: "fields", label: "Поля" });
      steps.push({ step: "preview", label: "Превью" });
      return steps;
    }
    steps.push({ step: "preset", label: "Пресет" });
    if (presetId === "custom") {
      steps.push({ step: "fields", label: "Поля" });
    }
    steps.push({ step: "preview", label: "Превью" });
    return steps;
  }, [isSingleRunShare, periodId, presetId]);

  const activeStepIndex = wizardSteps.findIndex((item) => item.step === step);

  const goToWizardStep = (target: WizardStep, targetIndex: number) => {
    if (targetIndex >= activeStepIndex) {
      return;
    }
    setStep(target);
  };

  const handleGenerate = async () => {
    if (!cardInput || !exportCanvasRef.current) {
      return;
    }
    setGenerating(true);
    setGenerateError(null);
    try {
      renderShareCard(exportCanvasRef.current, cardInput);
      const blob = await shareCardToBlob(exportCanvasRef.current);
      const safeName = shareUserDisplayName(user).replace(/[^\w\u0400-\u04FF.-]+/gu, "_").slice(0, 40);
      downloadShareBlob(blob, `share-${format.id}-${periodId}-${safeName || "stats"}.png`);
    } catch (err) {
      setGenerateError(err instanceof Error ? err.message : "Не удалось создать PNG");
    } finally {
      setGenerating(false);
    }
  };

  return (
    <AppShell title="Поделиться" activePath="/share">
      {loading && <p className="muted">Загрузка…</p>}

      {error && (
        <div className="card error">
          <p>{error}</p>
          <button type="button" className="btn secondary" onClick={() => void load()}>
            Повторить
          </button>
        </div>
      )}

      {!loading && !error && mode === "gallery" && (
        <>
          {!hasLinks && (
            <div className="card share-gallery-profile-hint">
              <p>Сначала привяжите хотя бы один профиль на главной — без данных карточка будет пустой.</p>
              <a className="btn primary" href="/dashboard#profiles">
                Перейти к привязке
              </a>
            </div>
          )}
          {hasLinks && dashboard?.stats && (
            <ShareGallery
              stats={dashboard.stats}
              runs={runs}
              displayName={shareUserDisplayName(user)}
              onCreate={startFreshWizard}
            />
          )}
        </>
      )}

      {!loading && !error && mode === "wizard" && !hasLinks && (
        <div className="card">
          <p>Сначала привяжите хотя бы один профиль на главной — без данных карточка будет пустой.</p>
          <div className="actions-row">
            <button type="button" className="btn secondary" onClick={backToGallery}>
              К примерам
            </button>
            <a className="btn primary" href="/dashboard#profiles">
              Перейти к привязке
            </a>
          </div>
        </div>
      )}

      {!loading && !error && mode === "wizard" && hasLinks && (
        <>
          <div className="share-wizard-toolbar">
            <button type="button" className="btn secondary" onClick={backToGallery}>
              ← К примерам
            </button>
          </div>

          <div className="share-steps" aria-label="Шаги">
            {wizardSteps.map(({ step: stepId, label }, index) => {
              const isActive = step === stepId;
              const isPast = index < activeStepIndex;
              return (
                <button
                  key={stepId}
                  type="button"
                  className={`share-step ${isActive ? "active" : ""} ${isPast ? "done" : ""}`}
                  disabled={!isPast}
                  onClick={() => goToWizardStep(stepId, index)}
                >
                  {index + 1}. {label}
                </button>
              );
            })}
          </div>

          {step === "format" && (
            <section className="card share-section">
              <h2 className="section-title">Куда публикуете?</h2>
              <div className="share-option-grid">
                {SHARE_FORMATS.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    className={`share-option-card ${formatId === item.id ? "selected" : ""}`}
                    onClick={() => setFormatId(item.id)}
                  >
                    <span className="share-option-title">{item.title}</span>
                    <span className="muted share-option-hint">{item.hint}</span>
                  </button>
                ))}
              </div>
              <div className="actions-row">
                <button type="button" className="btn primary" onClick={() => setStep("background")}>
                  Далее: фон
                </button>
              </div>
            </section>
          )}

          {step === "background" && (
            <section className="card share-section">
              <h2 className="section-title">Фон карточки</h2>
              <p className="muted share-section-lead">
                Формат: <strong>{format.title}</strong>. Готовые парковые фоны или своё фото (только в браузере).
              </p>
              {renderError && <p className="error-text">{renderError}</p>}
              <input
                ref={customFileInputRef}
                type="file"
                accept="image/*"
                className="share-file-input"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  event.target.value = "";
                  if (file) {
                    void handleCustomFile(file);
                  }
                }}
              />
              <div className="share-background-grid">
                {SHARE_BACKGROUNDS.map((item) => {
                  if (item.isCustom) {
                    const selected = backgroundId === item.id;
                    return (
                      <div
                        key={item.id}
                        role="button"
                        tabIndex={0}
                        className={`share-background-card share-background-card-custom ${selected ? "selected" : ""} ${customDragOver ? "drag-over" : ""}`}
                        title="Кадрирование будет доступно на следующем шаге"
                        aria-label="Своё фото"
                        onClick={() => selectCustomBackground()}
                        onKeyDown={(event) => {
                          if (event.key === "Enter" || event.key === " ") {
                            event.preventDefault();
                            selectCustomBackground();
                          }
                        }}
                        onDragEnter={(event) => {
                          event.preventDefault();
                          setCustomDragOver(true);
                        }}
                        onDragLeave={(event) => {
                          if (event.currentTarget.contains(event.relatedTarget as Node)) {
                            return;
                          }
                          setCustomDragOver(false);
                        }}
                        onDragOver={(event) => {
                          event.preventDefault();
                          setCustomDragOver(true);
                        }}
                        onDrop={(event) => {
                          event.preventDefault();
                          setCustomDragOver(false);
                          const file = event.dataTransfer.files[0];
                          if (file) {
                            void handleCustomFile(file);
                          }
                        }}
                      >
                        {customSourceImage ? (
                          <img
                            src={customSourceImage.src}
                            alt=""
                            className="share-background-thumb share-background-thumb-photo"
                            style={{ aspectRatio: thumbAspectRatio }}
                          />
                        ) : (
                          <span
                            className="share-background-thumb share-background-thumb-custom"
                            style={{ aspectRatio: thumbAspectRatio }}
                            aria-hidden="true"
                          >
                            <span>+</span>
                          </span>
                        )}
                      </div>
                    );
                  }

                  return (
                    <button
                      key={item.id}
                      type="button"
                      className={`share-background-card ${backgroundId === item.id ? "selected" : ""}`}
                      aria-label={`Фон ${item.title}`}
                      onClick={() => selectStockBackground(item.id)}
                    >
                      <span
                        className="share-background-thumb"
                        style={{
                          backgroundImage: `url(${item.url})`,
                          aspectRatio: thumbAspectRatio,
                        }}
                        aria-hidden="true"
                      />
                    </button>
                  );
                })}
              </div>

              {isSingleRunShare && isCustomBackground && customSourceImage && (
                <ShareBackgroundCrop
                  format={format}
                  initialSource={customSourceImage}
                  onApply={(image) => setCustomBackgroundImage(image)}
                  onSourceChange={(image) => {
                    setCustomSourceImage(image);
                    setCustomBackgroundImage(null);
                  }}
                />
              )}

              <div className="actions-row">
                <button type="button" className="btn secondary" onClick={() => setStep("format")}>
                  Назад
                </button>
                <button
                  type="button"
                  className="btn primary"
                  disabled={isCustomBackground && !customSourceImage}
                  onClick={() => setStep(isSingleRunShare ? "fields" : "period")}
                >
                  {isSingleRunShare ? "Далее: поля" : "Далее: период"}
                </button>
              </div>
            </section>
          )}

          {step === "period" && (
            <section className="card share-section">
              <h2 className="section-title">За какой период?</h2>
              <p className="muted share-section-lead">
                {format.title}
                {isCustomBackground ? " · своё фото" : ` · фон «${background.title}»`}. Выберите, какую сводку
                показать на карточке.
              </p>

              {isCustomBackground && customSourceImage && (
                <ShareBackgroundCrop
                  format={format}
                  initialSource={customSourceImage}
                  onApply={(image) => setCustomBackgroundImage(image)}
                  onSourceChange={(image) => {
                    setCustomSourceImage(image);
                    setCustomBackgroundImage(null);
                  }}
                />
              )}

              <div className="share-option-grid">
                {SHARE_PERIODS.map((item) => {
                  const description =
                    item.id === "year" ? `С 1 января ${CURRENT_YEAR} года` : item.description;
                  const disabled = item.id === "last_run" && !lastRun;
                  return (
                    <button
                      key={item.id}
                      type="button"
                      className={`share-option-card ${periodId === item.id ? "selected" : ""}`}
                      disabled={disabled}
                      onClick={() => setPeriodId(item.id)}
                    >
                      <span className="share-option-title">
                        {item.id === "year" ? `За ${CURRENT_YEAR} год` : item.title}
                      </span>
                      <span className="muted share-option-hint">
                        {disabled ? "Нет пробежек в истории" : description}
                      </span>
                    </button>
                  );
                })}
              </div>
              <div className="actions-row">
                <button type="button" className="btn secondary" onClick={() => setStep("background")}>
                  Назад
                </button>
                <button
                  type="button"
                  className="btn primary"
                  disabled={periodId === "last_run" && !lastRun}
                  onClick={handlePeriodContinue}
                >
                  {periodId === "last_run" ? "Далее: поля" : "Далее: пресет"}
                </button>
              </div>
            </section>
          )}

          {step === "preset" && periodId !== "last_run" && (
            <section className="card share-section">
              <h2 className="section-title">Готовый набор полей</h2>
              <p className="muted share-section-lead">
                {format.title} · фон «{background.title}» · {periodTitle.toLowerCase()}. На карточке — не
                больше 3 показателей.
              </p>
              <div className="share-option-grid share-preset-grid">
                {SHARE_PRESETS.map((preset) => (
                  <button
                    key={preset.id}
                    type="button"
                    className={`share-option-card ${presetId === preset.id ? "selected" : ""}`}
                    onClick={() => handleSelectPreset(preset.id)}
                  >
                    <span className="share-option-title">{preset.title}</span>
                    <span className="muted share-option-hint">{preset.description}</span>
                    {preset.fields.length > 0 && (
                      <span className="share-preset-fields muted">
                        {preset.fields
                          .slice(0, format.maxFields)
                          .map((id) => {
                            const label = SHARE_FIELDS.find((f) => f.id === id)?.label ?? id;
                            return preset.primaryFieldId === id ? `${label} · главная` : label;
                          })
                          .join(" · ")}
                      </span>
                    )}
                  </button>
                ))}
              </div>
              <div className="actions-row">
                <button type="button" className="btn secondary" onClick={() => setStep("period")}>
                  Назад
                </button>
              </div>
            </section>
          )}

          {step === "fields" && isRunCard && (
            <section className="card share-section">
              <h2 className="section-title">Что показать на карточке</h2>
              <p className="muted share-section-lead">
                Выберите до {format.maxFields} показателей пробежки для формата «{format.title}».
              </p>
              <ul className="share-field-list">
                {RUN_SHARE_FIELDS.map((field) => {
                  const available = singleRun ? buildRunMetricRow(singleRun, field.id) !== null : false;
                  const checked = runFields.includes(field.id);
                  const disabled =
                    !available || (!checked && runFields.length >= format.maxFields);
                  return (
                    <li key={field.id}>
                      <label className={`share-field-row ${disabled ? "disabled" : ""}`}>
                        <input
                          type="checkbox"
                          checked={checked && available}
                          disabled={disabled}
                          onChange={() => toggleRunField(field.id)}
                        />
                        <span>
                          <span className="share-field-label">{field.label}</span>
                          <span className="muted share-field-hint">
                            {available ? field.hint : "Нет данных для этой пробежки"}
                          </span>
                        </span>
                      </label>
                    </li>
                  );
                })}
              </ul>
              <div className="actions-row">
                <button
                  type="button"
                  className="btn secondary"
                  onClick={() => setStep(isSingleRunShare ? "background" : "period")}
                >
                  Назад
                </button>
                <button
                  type="button"
                  className="btn primary"
                  disabled={runFields.length === 0}
                  onClick={() => setStep("preview")}
                >
                  Превью
                </button>
              </div>
            </section>
          )}

          {step === "fields" && !isRunCard && presetId === "custom" && (
            <section className="card share-section">
              <h2 className="section-title">Свои поля</h2>
              <p className="muted share-section-lead">
                Выберите до {format.maxFields} показателей для формата «{format.title}» · {periodTitle.toLowerCase()}
                .
              </p>
              <ul className="share-field-list">
                {SHARE_FIELDS.map((field) => {
                  const checked = selectedFields.includes(field.id);
                  const disabled = !checked && selectedFields.length >= format.maxFields;
                  return (
                    <li key={field.id}>
                      <label className={`share-field-row ${disabled ? "disabled" : ""}`}>
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={disabled}
                          onChange={() => toggleField(field.id)}
                        />
                        <span>
                          <span className="share-field-label">{field.label}</span>
                          <span className="muted share-field-hint">{field.hint}</span>
                        </span>
                      </label>
                    </li>
                  );
                })}
              </ul>
              <div className="actions-row">
                <button type="button" className="btn secondary" onClick={() => setStep("preset")}>
                  Назад
                </button>
                <button
                  type="button"
                  className="btn primary"
                  disabled={selectedFields.length === 0}
                  onClick={() => setStep("preview")}
                >
                  Превью
                </button>
              </div>
            </section>
          )}

          {step === "preview" && !cardInput && periodId === "last_run" && (
            <section className="card share-section">
              <h2 className="section-title">Превью</h2>
              <p className="error-text">Нет пробежек — карточку для последнего старта собрать не получится.</p>
              <div className="actions-row">
                <button
                  type="button"
                  className="btn secondary"
                  onClick={() => setStep(isSingleRunShare ? "background" : "period")}
                >
                  Назад
                </button>
              </div>
            </section>
          )}

          {step === "preview" && cardInput && (
            <section className="card share-section">
              <h2 className="section-title">Превью</h2>
              <p className="muted share-section-lead">
                {format.title} · фон «{background.title}» ·{" "}
                {isSingleRunShare
                  ? targetAccentLabel
                    ? "веха из «Моей истории»"
                    : "пробежка из «В этот день»"
                  : periodTitle.toLowerCase()}
                {!isSingleRunShare && periodId !== "last_run" && presetId && presetId !== "custom"
                  ? ` · пресет «${SHARE_PRESETS.find((p) => p.id === presetId)?.title}»`
                  : !isSingleRunShare && periodId !== "last_run"
                    ? " · свои поля"
                    : ""}
              </p>
              <div className="share-text-color" role="group" aria-label="Цвет текста на фото">
                <span className="share-text-color-label">Цвет текста</span>
                <div className="share-text-color-options">
                  {availableTextColors.map((option) => (
                    <button
                      key={option.id}
                      type="button"
                      className={`share-text-color-btn ${textColor === option.id ? "selected" : ""}`}
                      aria-pressed={textColor === option.id}
                      onClick={() => setTextColor(option.id)}
                    >
                      <span
                        className="share-text-color-swatch"
                        style={{ backgroundColor: option.swatch }}
                        aria-hidden="true"
                      />
                      {option.label}
                    </button>
                  ))}
                </div>
              </div>
              <div className="share-preview-wrap">
                <canvas
                  ref={previewCanvasRef}
                  className="share-preview-canvas"
                  style={{ aspectRatio: `${format.width} / ${format.height}` }}
                />
              </div>
              <canvas ref={exportCanvasRef} className="share-export-canvas" aria-hidden="true" />
              {generateError && <p className="error-text">{generateError}</p>}
              <div className="actions-row">
                <button
                  type="button"
                  className="btn secondary"
                  onClick={() =>
                    setStep(
                      isRunCard ? "fields" : presetId === "custom" ? "fields" : "preset",
                    )
                  }
                >
                  Назад
                </button>
                {!isSingleRunShare && (
                  <button type="button" className="btn secondary" onClick={() => setStep("background")}>
                    Сменить фон
                  </button>
                )}
                <button
                  type="button"
                  className="btn primary"
                  disabled={generating || !cardInput.metrics.some((metric) => metric != null)}
                  onClick={() => void handleGenerate()}
                >
                  {generating ? "Создание…" : "Скачать PNG"}
                </button>
              </div>
              <p className="muted share-footnote">
                Имя на карточке: {shareUserDisplayName(user)}. Изменить можно в шапке сайта или в{" "}
                <a href="/settings">настройках</a>.
              </p>
            </section>
          )}
        </>
      )}
    </AppShell>
  );
}

export function SharePage() {
  return <RequireAuth>{() => <ShareContent />}</RequireAuth>;
}
