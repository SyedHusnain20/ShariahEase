---
name: ShariahEase
colors:
  surface: '#12131a'
  surface-dim: '#12131a'
  surface-bright: '#383940'
  surface-container-lowest: '#0c0e14'
  surface-container-low: '#1a1b22'
  surface-container: '#1e1f26'
  surface-container-high: '#282a31'
  surface-container-highest: '#33343c'
  on-surface: '#e2e1eb'
  on-surface-variant: '#bbcabf'
  inverse-surface: '#e2e1eb'
  inverse-on-surface: '#2f3037'
  outline: '#86948a'
  outline-variant: '#3c4a42'
  surface-tint: '#4edea3'
  primary: '#4edea3'
  on-primary: '#003824'
  primary-container: '#10b981'
  on-primary-container: '#00422b'
  inverse-primary: '#006c49'
  secondary: '#c8c6c5'
  on-secondary: '#313030'
  secondary-container: '#4a4949'
  on-secondary-container: '#bab8b7'
  tertiary: '#c8c6c5'
  on-tertiary: '#313030'
  tertiary-container: '#a4a2a2'
  on-tertiary-container: '#393939'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#6ffbbe'
  primary-fixed-dim: '#4edea3'
  on-primary-fixed: '#002113'
  on-primary-fixed-variant: '#005236'
  secondary-fixed: '#e5e2e1'
  secondary-fixed-dim: '#c8c6c5'
  on-secondary-fixed: '#1c1b1b'
  on-secondary-fixed-variant: '#474646'
  tertiary-fixed: '#e5e2e1'
  tertiary-fixed-dim: '#c8c6c5'
  on-tertiary-fixed: '#1b1b1b'
  on-tertiary-fixed-variant: '#474746'
  background: '#12131a'
  on-background: '#e2e1eb'
  surface-variant: '#33343c'
typography:
  headline-xl:
    fontFamily: Inter
    fontSize: 36px
    fontWeight: '700'
    lineHeight: 44px
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
    letterSpacing: -0.015em
  headline-md:
    fontFamily: Inter
    fontSize: 20px
    fontWeight: '600'
    lineHeight: 28px
  body-lg:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  body-md:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  label-md:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '500'
    lineHeight: 16px
    letterSpacing: 0.01em
  urdu-body:
    fontFamily: Noto Nastaliq Urdu
    fontSize: 18px
    fontWeight: '400'
    lineHeight: 32px
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  sidebar-width: 208px
  gutter: 16px
  margin-mobile: 16px
  margin-desktop: 32px
  stack-sm: 4px
  stack-md: 8px
  stack-lg: 16px
---

## Brand & Style
The design system embodies "Ethical Precision"—a synthesis of modern financial rigor and Islamic principles. The brand personality is authoritative yet accessible, designed for a target audience of high-net-worth investors, financial advisors, and tech-savvy users in Pakistan seeking Shariah-compliant insights.

The visual style is **Corporate Modern** with heavy influences from the **Minimalist** and **Linear/Vercel** aesthetics. It prioritizes high information density, extreme legibility, and a sophisticated dark interface that feels like a premium tool for serious decision-making. The UI evokes feelings of trust, stability, and spiritual alignment through a refined, data-centric presentation.

## Colors
This design system utilizes a deep-space dark palette to reduce eye strain and emphasize primary action areas. The primary accent is **Emerald 500**, used sparingly to signify growth and Islamic identity.

- **Background & Surface:** Use the deepest black (`#0a0a0a`) for the base and tiered grays (`#141414`, `#1c1c1c`) for cards and elevated components to create structural depth without using light.
- **Borders:** A consistent 1px border (`#262626`) is the primary method for defining element boundaries.
- **Semantic Logic:** Status colors are strictly reserved for Shariah compliance ratings. Halal (Green) indicates compliance, Haram (Red) indicates non-compliance, and Doubtful (Amber) signals items requiring further purification or screening.

## Typography
The system relies on **Inter** for its neutral, highly legible character, facilitating the "Linear" financial look. Typography is tight and structured with subtle negative letter-spacing on larger headings to maintain a "technical" feel.

For localized content, **Noto Nastaliq Urdu** is integrated for maximum cultural authenticity and readability. Urdu text requires a larger base font size (18px) and increased line height (32px) compared to English to accommodate the verticality of the script. Labels and data points should always use the `label-md` style in medium weight to maintain clarity in high-density dashboards.

## Layout & Spacing
The layout follows a **Fixed-Fluid** model. A global sidebar is fixed at 208px (52 units), providing a persistent anchor for navigation. The main content area utilizes a 12-column grid that expands to fill the remaining horizontal space.

- **Sidebar:** Fixed width of 208px. Contains primary navigation and AI assistant shortcuts.
- **Grid:** On desktop, use a 32px outer margin and 16px gutters. On mobile, margins scale down to 16px.
- **Density:** Information density is high. Use 8px (stack-md) as the base spacing unit for internal card padding and vertical separation between related data rows. 
- **Alignment:** All headers and card titles must align to the left of the content grid, while financial figures and status chips should be right-aligned within their respective columns for easier scanning.

## Elevation & Depth
Depth is created through **Tonal Layering** rather than traditional shadows. In this dark-mode environment, shadows are replaced by 1px borders and subtle changes in surface color to indicate hierarchy.

- **Level 0 (Background):** #0a0a0a. Used for the main canvas.
- **Level 1 (Surface):** #141414. Used for primary dashboard cards and the sidebar.
- **Level 2 (Elevated):** #1c1c1c. Used for modals, dropdown menus, and popovers.
- **Accents:** For high-priority elements like active buttons or focused inputs, use a subtle 1px inner glow or a primary-colored border stroke (`#10b981`) to draw attention without breaking the minimalist aesthetic.

## Shapes
The shape language is professional and modern, favoring structured geometry with soft edges. 

Cards and major containers utilize the `rounded-xl` token (12px) to create a approachable yet sophisticated silhouette. Buttons and smaller UI elements like input fields follow the base `roundedness: 2` (8px) for consistency. This balance ensures the UI feels "financial-grade" (not too circular/playful) while maintaining the modern "SaaS" look.

## Components
- **Buttons:** Primary buttons use a solid Emerald background with dark text. Secondary buttons use a transparent background with a #262626 border.
- **Cards:** Defined by #141414 background and #262626 border. Use a 12px corner radius. Headlines inside cards should be `headline-md`.
- **Status Chips:** Small, pill-shaped indicators. Use a low-opacity background of the status color (e.g., Green at 10% opacity) with a high-contrast text color for the label.
- **Inputs:** Dark background (#0a0a0a) with a subtle #262626 border. On focus, the border transitions to the primary Emerald (#10b981) with a zero-spread 1px outer glow.
- **Lists:** Data-heavy lists should use alternating row highlights or subtle separators (#262626). Ensure financial values use monospaced numerals if possible for vertical alignment.
- **AI Assistant Chat:** Use the `#1c1c1c` surface for the assistant's bubbles and `#141414` for the user's bubbles. Keep the interface focused with minimal iconography.
