// 图标路径来自静态原型（本地 SVG，无外部依赖）。
const paths: Record<string, string> = {
  grid: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
  plus: "M12 5v14 M5 12h14",
  spark: "m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z M20 2v4 M18 4h4",
  folder: "M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
  settings: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1z",
  leaf: "M20 3C9 2 3 7 5 15c4 7 15 3 15-12z M5 20l9-10",
  left: "m14 6-6 6 6 6",
  right: "m10 6 6 6-6 6",
  arrow: "M5 12h14 m-5-5 5 5-5 5",
  card: "M3 5h18v14H3z M3 9h18 M6 15h4",
  cash: "M3 6h18v12H3z M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6",
  wallet: "M3 6h16v14H3z M3 6V4h13v2 M15 10h6v6h-6z",
  bag: "M4 7h16l-1 14H5z M8 8V6a4 4 0 0 1 8 0v2",
  income: "M12 3v15 m-5-5 5 5 5-5 M4 18v3h16v-3",
  search: "M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14 M15 15l6 6",
  trash: "M4 6h16 M9 6V3h6v3 M6 6l1 15h10l1-15 M10 10v7 M14 10v7",
  edit: "m4 16 12-12 4 4-12 12H4z M14 6l4 4",
  upload: "M12 16V3 m-5 5 5-5 5 5 M4 15v6h16v-6",
  info: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M12 10v7 M12 7v.1",
  chat: "M3 4h18v13H9l-6 4z M7 8h10 M7 12h7",
  check: "m5 12 4 4L19 6",
  download: "M12 3v13 m-5-5 5 5 5-5 M4 17v4h16v-4",
  home: "m3 10 9-7 9 7 M5 9v12h14V9 M10 21v-7h4v7",
  user: "M12 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M4 21c1-5 15-5 16 0",
  rate: "M4 7h13 m-4-4 4 4-4 4 M20 17H7 m4-4-4 4 4 4",
  undo: "M9 14 4 9l5-5 M4 9h11a5 5 0 0 1 0 10h-3",
  logout: "M15 3h4v18h-4 M10 17l5-5-5-5 M15 12H3",
  menu: "M4 6h16 M4 12h16 M4 18h16",
};

export function Icon({ name }: { name: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={paths[name] ?? paths.folder} />
    </svg>
  );
}
