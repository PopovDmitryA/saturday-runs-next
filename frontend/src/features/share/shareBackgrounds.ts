export type ShareBackgroundId =
  | "custom"
  | "park_alley"
  | "park_lane"
  | "park_path"
  | "park_walkway"
  | "park_bench"
  | "park_trees"
  | "park_morning"
  | "park_autumn"
  | "park_trail"
  | "park_meadow"
  | "park_lake"
  | "park_sunset"
  | "park_green"
  | "park_boardwalk"
  | "park_foliage"
  | "park_stream"
  | "park_hill"
  | "park_dawn";

export type ShareBackground = {
  id: ShareBackgroundId;
  title: string;
  url: string | null;
  isCustom?: boolean;
};

export const SHARE_CUSTOM_BACKGROUND: ShareBackground = {
  id: "custom",
  title: "Своё фото",
  url: null,
  isCustom: true,
};

/** Stock park backgrounds for share cards. */
export const SHARE_STOCK_BACKGROUNDS: ShareBackground[] = [
  {
    id: "park_alley",
    title: "Парк",
    url: "/share-backgrounds/bg-park-alley.jpg",
  },
  {
    id: "park_lane",
    title: "Аллея",
    url: "/share-backgrounds/bg-park-lane.jpg",
  },
  {
    id: "park_path",
    title: "Дорожка",
    url: "/share-backgrounds/bg-park-path.jpg",
  },
  {
    id: "park_walkway",
    title: "Прогулочная",
    url: "/share-backgrounds/bg-park-walkway.jpg",
  },
  {
    id: "park_bench",
    title: "Скамейка",
    url: "/share-backgrounds/bg-park-bench.jpg",
  },
  {
    id: "park_trees",
    title: "Деревья",
    url: "/share-backgrounds/bg-park-trees-2.jpg",
  },
  {
    id: "park_morning",
    title: "Утро",
    url: "/share-backgrounds/bg-park-morning.jpg",
  },
  {
    id: "park_autumn",
    title: "Осень",
    url: "/share-backgrounds/bg-park-autumn.jpg",
  },
  {
    id: "park_trail",
    title: "Тропинка",
    url: "/share-backgrounds/bg-park-trail.jpg",
  },
  {
    id: "park_meadow",
    title: "Луг",
    url: "/share-backgrounds/bg-park-meadow.jpg",
  },
  {
    id: "park_lake",
    title: "У воды",
    url: "/share-backgrounds/bg-park-lake.jpg",
  },
  {
    id: "park_sunset",
    title: "Закат",
    url: "/share-backgrounds/bg-park-sunset.jpg",
  },
  {
    id: "park_green",
    title: "Зелень",
    url: "/share-backgrounds/bg-park-green.jpg",
  },
  {
    id: "park_boardwalk",
    title: "Настил",
    url: "/share-backgrounds/bg-park-boardwalk.jpg",
  },
  {
    id: "park_foliage",
    title: "Листва",
    url: "/share-backgrounds/bg-park-foliage.jpg",
  },
  {
    id: "park_stream",
    title: "Ручей",
    url: "/share-backgrounds/bg-park-stream.jpg",
  },
  {
    id: "park_hill",
    title: "Холм",
    url: "/share-backgrounds/bg-park-hill.jpg",
  },
  {
    id: "park_dawn",
    title: "Рассвет",
    url: "/share-backgrounds/bg-park-dawn.jpg",
  },
];

export const SHARE_BACKGROUNDS: ShareBackground[] = [
  SHARE_CUSTOM_BACKGROUND,
  ...SHARE_STOCK_BACKGROUNDS,
];

const imageCache = new Map<string, HTMLImageElement>();

export function getShareBackground(id: ShareBackgroundId): ShareBackground {
  return SHARE_BACKGROUNDS.find((item) => item.id === id) ?? SHARE_STOCK_BACKGROUNDS[0];
}

export function isCustomBackgroundId(id: ShareBackgroundId): boolean {
  return id === "custom";
}

export function loadShareBackgroundImage(background: ShareBackground): Promise<HTMLImageElement> {
  if (!background.url) {
    return Promise.reject(new Error("Для своего фото загрузите файл"));
  }
  const cached = imageCache.get(background.url);
  if (cached) {
    return Promise.resolve(cached);
  }
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      imageCache.set(background.url!, img);
      resolve(img);
    };
    img.onerror = () => reject(new Error(`Не удалось загрузить фон «${background.title}»`));
    img.src = background.url!;
  });
}

export function preloadShareBackgrounds(): void {
  for (const bg of SHARE_STOCK_BACKGROUNDS) {
    if (bg.url) {
      void loadShareBackgroundImage(bg).catch(() => undefined);
    }
  }
}

export function clearStockBackgroundCache(): void {
  imageCache.clear();
}
