# AI Rules

## Tech Stack

- **React + TypeScript**: All UI is built with React function components and typed with TypeScript.
- **Vite**: Build tool and dev server.
- **React Router**: Client-side routing; all routes live in `src/App.tsx`.
- **shadcn/ui + Radix UI**: Pre-installed component library for all UI primitives.
- **Tailwind CSS**: The only styling approach.
- **lucide-react**: Icon set.
- **Project structure**: Everything lives in `src/`. Pages in `src/pages/`, components in `src/components/`. The default page is `src/pages/Index.tsx`.

## Library Rules

| Need | Use |
|------|-----|
| UI primitives (buttons, dialogs, inputs, cards, tabs, etc.) | shadcn/ui components from `src/components/ui/`. Do not edit these files; wrap them in new components instead. |
| Styling, layout, spacing, colors | Tailwind CSS classes. No inline styles or custom CSS unless unavoidable. |
| Icons | `lucide-react` only. |
| Routing / navigation | `react-router-dom`. Keep routes in `src/App.tsx`. |
| Accessible low-level behavior (popovers, menus, etc.) | Radix UI, already installed (usually via shadcn/ui). |

## General Conventions

- Create small, focused components in their own files.
- Always add new pages or components to the app (e.g. render them from `src/pages/Index.tsx`), otherwise they won't be visible.
- Don't install new packages if shadcn/ui, Tailwind, or lucide-react already covers the need.
- Write responsive layouts by default, using Tailwind's responsive prefixes.
