// The app's shared style: the font, text and icon sizes, spacing, corner radii, element sizes, colors.
// All styling comes from here and changes in one place — applied right away across every section.
// Screens don't write their own .font(.caption), .padding(10), cornerRadius: 8 or colors like .red: tests/test_mac_style.py checks this.
// Table column widths and input field widths are set where they're used, that's layout, not style.
import SwiftUI

enum Style {
    /// One font for the whole app — system San Francisco. Set it to .rounded and the rounded variant shows up everywhere at once.
    static let design: Font.Design = .default

    /// One multiplier for all text and icons (Text.*, Icon.*): 1.1 — everything a bit bigger, 0.9 — smaller. It doesn't change
    /// element sizes (Size.*, controlHeight): adjust those too at a large value
    static let scale: CGFloat = 1.0

    /// Text: six sizes, every typeface is built from them
    enum Text {
        static let sizeDisplay: CGFloat = 17 * Style.scale   // large numbers, titles of empty screens
        static let sizeTitle: CGFloat = 15 * Style.scale     // a block's title
        static let sizeBody: CGFloat = 13 * Style.scale      // main text, menu rows, tables
        static let sizeSmall: CGFloat = 12 * Style.scale     // secondary text, menu tabs
        static let sizeCaption: CGFloat = 11 * Style.scale   // labels
        static let sizeTiny: CGFloat = 10 * Style.scale      // counters, utility text

        static let display = Font.system(size: sizeDisplay, weight: .semibold, design: Style.design)
        static let title = Font.system(size: sizeTitle, weight: .semibold, design: Style.design)
        static let heading = Font.system(size: sizeBody, weight: .semibold, design: Style.design)
        static let body = Font.system(size: sizeBody, weight: .regular, design: Style.design)
        static let small = Font.system(size: sizeSmall, weight: .regular, design: Style.design)
        static let caption = Font.system(size: sizeCaption, weight: .regular, design: Style.design)
        static let tiny = Font.system(size: sizeTiny, weight: .regular, design: Style.design)
        static let shrink: CGFloat = 0.7                                                   // how much a number in a tile can shrink to avoid wrapping
        static let mono = Font.system(size: sizeSmall, weight: .regular, design: .monospaced)         // code, logs, paths
        static let monoSmall = Font.system(size: sizeCaption, weight: .regular, design: .monospaced)
    }

    /// SF Symbols icons: size and line weight
    enum Icon {
        static let weight: Font.Weight = .light
        static let menu = Font.system(size: 14 * Style.scale, weight: weight)       // menu sections
        static let menuWidth: CGFloat = 18 * Style.scale                             // room for the icon in a menu row
        static let sub = Font.system(size: 12 * Style.scale, weight: weight)        // small icons in rows
        static let inline = Font.system(size: 13 * Style.scale, weight: .regular)   // icons in text and buttons
        static let tiny = Font.system(size: 10 * Style.scale, weight: .medium)      // "+" and "−" in the menu
        static let switchMark = Font.system(size: 10 * Style.scale, weight: .heavy) // the checkmark and x-mark in a switch's knob
        static let big = Font.system(size: 28 * Style.scale, weight: weight)        // empty screens
        static let windowButton = Font.system(size: 7 * Style.scale, weight: .bold) // icons on window buttons (close, minimize, zoom) on hover
        static let composer = Font.system(size: 15 * Style.scale, weight: .regular) // the paperclip and mic in chat
        static let send = Font.system(size: 14 * Style.scale, weight: .semibold)    // the send arrow
        static let chip = Font.system(size: 18 * Style.scale, weight: weight)       // a file icon in an attachment
        static let empty = Font.system(size: 34 * Style.scale, weight: .ultraLight) // empty chat
        static let leafToolbar: CGFloat = 15 * Style.scale             // Sena's leaf in the toolbar
        static let leafMenu: CGFloat = 17 * Style.scale                // the leaf in the menu header
        static let leafTile: CGFloat = 76 * Style.scale                // the leaf on a tile, while there's no connection
    }

    /// Spacing: one scale for everything
    enum Space {
        static let xxs: CGFloat = 2
        static let xs: CGFloat = 4
        static let s: CGFloat = 6
        static let m: CGFloat = 8
        static let l: CGFloat = 10
        static let xl: CGFloat = 12
        static let xxl: CGFloat = 16
        static let huge: CGFloat = 20
        // THE SPACING STANDARD: from a block's edge to its content — inset, between elements — gap, between rows — gapSmall
        static let inset: CGFloat = 16
        static let gap: CGFloat = 12
        static let gapSmall: CGFloat = 8
    }

    /// One corner radius for the whole app: cards, blocks, buttons, chips, bubbles, fields, tiles. Changed with one number
    /// (no per-element names: they were all this one, and the style test keeps it so)
    enum Radius {
        static let all: CGFloat = 12
        static let notchTop: CGFloat = 6   // the concave corner at the notch shelf — matches the notch's own shape (the bottom uses the ordinary all)
    }

    /// Element sizes
    enum Size {
        static let minWidth: CGFloat = 760         // the window is never narrower or shorter than this
        static let minHeight: CGFloat = 560
        static let compactWindow: CGFloat = 1100   // narrower than this — the menu collapses to icons, columns and block rows stack vertically
        static let menuWidth: CGFloat = 216        // the left block
        static let menuNarrow: CGFloat = 64        // the left block in a narrow window: icons only
        static let column: CGFloat = 340           // a section's left column (a list), with details on the right
        static let columnWide: CGFloat = 420       // a wider left column (tasks) and the width of popovers
        static let sheet: CGFloat = 560            // a small edit sheet
        static let entry: CGFloat = 520            // no narrower: record cards in a grid are two columns in a wide window, one in a narrow one
        static let badge: CGFloat = 40             // the round kind icon on the left of a card: the same for every record
        static let bar: CGFloat = 6                // a progress bar (budget)
        static let flowCard: CGFloat = 260         // a command card on the process diagram
        static let flowNode: CGFloat = 220         // a stage node on the diagram
        static let flowHeight: CGFloat = 108       // every diagram card is the same height
        static let band: CGFloat = 4               // the colored band at the top of a diagram card
        static let connector: CGFloat = 32         // the line between diagram nodes
        static let gridStep: CGFloat = 24          // the dot grid's step under the diagram
        static let header = controlHeight + 2 * Space.gapSmall   // headers: the element plus the standard gap above and below (32 + 8 + 8)
        static let search: CGFloat = 320           // the shared search field in the header stays a constant width, so nothing else jumps around
        static let searchNarrow: CGFloat = 128   // a screen's search field in a narrow window header (tabs and filters sit alongside it too)
        static let windowButton: CGFloat = 12      // a window button (close, minimize, zoom)
        static let select: CGFloat = 168           // a dropdown list: ONE width for all of them (short, fast, Sunday, whoever)
        static let selectShort: CGFloat = 112      // a list of a number or a time ("60 min", "9:00")
        static let date: CGFloat = 136             // a date field
        static let field: CGFloat = 200            // a search field in the header
        static let listMax: CGFloat = 340          // beyond this a list stops growing and scrolls: a dropdown list, a list above details in a narrow window
        static let loader: CGFloat = 62            // the centered loading leaf
        static let spinner: CGFloat = 12           // a small inline loading ring
        static let chatWidth: CGFloat = 960        // the conversation column and the input block: narrower than the window, centered, with margins on the sides
        static let bubbleMax: CGFloat = 640        // a chat bubble goes no wider than this
        static let bubbleGap: CGFloat = 80         // empty space on the other side of a bubble
        static let thumb: CGFloat = 220            // an image in a bubble goes no taller than this
        static let thumbWide: CGFloat = 320        // nor wider
        static let chip: CGFloat = 44              // an attachment in the row above the input field
        static let moneyWidth: CGFloat = 64        // the money column in cost lists (the ring, the most expensive tasks, the records table)
        static let button = controlHeight          // chat's round buttons and icons are the same height
        static let waveBars = 28                   // bars in the recording's live wave
        static let waveBar: CGFloat = 3
        static let waveHeight: CGFloat = 22
        static let counter: CGFloat = 17           // a counter circle
        static let notchFallback: CGFloat = 150    // the hover zone's width by the notch, if there's no notch screen and its size can't be found any other way
        static let notchSide: CGFloat = 132        // a side block by the notch: the agents' characters on the left, the tab icons on the right; equal, so the gap sits exactly under the notch
        static let dot: CGFloat = 9                // a status dot
        static let line: CGFloat = 1               // the connecting line between tabs
        static let lineDot: CGFloat = 5            // the dot on the line across from the active tab
        static let tile: CGFloat = 110             // the tile with a leaf, while there's no connection
        static let control: ControlSize = .regular   // one size for system elements (fields, switches, lists) across the whole window
        static let controlHeight: CGFloat = 32       // ONE height for small elements: buttons, chips, icons, the title block in the header
        static let switchWidth: CGFloat = 46         // a switch: the track
        static let switchHeight: CGFloat = 28
        static let switchKnob: CGFloat = 22          // the knob
        static let switchNotch: CGFloat = 0.75       // switches on the notch shelf are this many times smaller than usual
        static let shadowRadius: CGFloat = 10      // cards' shadow
        static let shadowY: CGFloat = 3
        static let shadowOpacity: Double = 0.10
        static let borderOpacity: Double = 0.12    // cards' and blocks' border — a visible one
    }

    /// Motion: a spring for rearranging, a short fade for hovering. Screens animate through .motion(value:) (Views/Motion.swift):
    /// with the system's "reduce motion" there's no animation, infinite ones (a ring, shimmer, the leaf's breathing) don't start
    enum Motion {
        static let spring = Animation.spring(response: 0.38, dampingFraction: 0.78)   // the "pill" moves, tabs expand
        static let quick = Animation.easeOut(duration: 0.16)                           // hover, taps, tooltips
        static let pressScale: CGFloat = 0.97                                           // a pressed button or row
        static let hoverShift: CGFloat = 2                                              // a row's shift on hover
        static let pulse = Animation.easeOut(duration: 1.8).repeatForever(autoreverses: false)      // the ring from a "running" dot
        static let pulseReach: CGFloat = 2.4        // how many times bigger than the dot the ring ends up
        static let pulseOpacity: Double = 0.7       // the brightness it starts fading from
        static let typing = Animation.easeInOut(duration: 0.5).repeatForever(autoreverses: true)    // the "Sena is typing" dots
        static let breathe = Animation.easeInOut(duration: 1.1).repeatForever(autoreverses: true)   // a dot breathing on the notch capsule
        static let typingStep: Double = 0.16                                                        // the offset between dots
        static let blink = Animation.easeInOut(duration: 0.8).repeatForever(autoreverses: true)     // the red recording dot
        // Appearing and hovering — like in costs: everything in the app behaves the same way, changed here
        static let rise = Animation.spring(response: 0.55, dampingFraction: 0.82)   // a block slides up from below when a section opens
        static let riseShift: CGFloat = 16          // how deep a block starts sliding in from
        static let riseScale: CGFloat = 0.985       // and how much smaller it starts out
        static let riseScaleSmall: CGFloat = 0.992  // a list card
        static let openWindow: Double = 0.9         // how many seconds after a section opens, blocks cascade in
        static let emptyDelay: Double = 0.12        // an empty screen fades in a bit after the cascade
        static let riseStagger: Double = 0.0004     // the delay per point of height: blocks build up top to bottom
        static let riseMax: Double = 0.4            // the biggest delay
        static let riseTop: CGFloat = 120           // the header's height: the delay is computed from it
        static let riseBottom: CGFloat = 1200       // a fallback screen height, when it can't be found: below this, blocks appear instantly
        static let slide = Animation.spring(response: 0.34, dampingFraction: 0.86)   // the selected option's pill moves to its neighbor
        static let lift: CGFloat = 1.012            // a card on hover
        static let liftShadow: Double = 0.18        // and its shadow
        static let hoverScale: CGFloat = 1.03       // a tile, a chip, a field under the cursor
        static let buttonHover: CGFloat = 1.025     // a button under the cursor
        static let hoverLight: Double = 0.14        // how much the fill lightens under the cursor
        static let hoverRing: Double = 0.55         // the hover border on a field
        static let sendIdle: CGFloat = 0.9          // the send arrow, while there's nothing to send
        static let glow: Double = 0.38              // a button's glow under the cursor
        static let listStagger: Double = 0.05       // the delay between a list's placeholder cards
        static let loadingDelay: Double = 0.18      // the loading leaf doesn't show up right away: fast replies without a flash
        static let loadingScale: CGFloat = 0.8      // and it grows from this size
        static let barDelay: Double = 0.2           // a progress bar starts growing a bit after the block appears
        static let glyphStep: Double = 0.085        // one segment of an icon's motion, in seconds (each icon has as many segments as it needs)
        static let glyphSettle: Double = 0.38       // and the spring back to neutral
        static let glyphPerspective: CGFloat = 0.55 // the depth when a coin or a card flips
        static let switchSpring = Animation.spring(response: 0.38, dampingFraction: 0.6)   // a switch's knob overshoots and settles
        static let switchStretch: CGFloat = 1.32    // the knob stretches while it's held
        static let burstReach: CGFloat = 2.2        // the ring from a switch turning on grows this many times bigger
        static let burstTime: Double = 0.65         // and fades out over this many seconds
        static let burstOpacity: Double = 0.6       // the brightness it starts from
        static let switchOff: Double = 0.24         // a switched-off switch's track: ink with transparency
        static let disabledFade: Double = 0.4       // a disabled element gets paler
        static let pressedFade: Double = 0.8        // and a button being pressed, a little
        static let plateFast = Animation.spring(response: 0.24, dampingFraction: 0.86)    // for a tab's pill, the edge closer to the target catches up fast,
        static let plateSlow = Animation.spring(response: 0.5, dampingFraction: 0.8)      // the far one lags behind: the pill stretches and then gathers back
        static let pageSlide: CGFloat = 34          // a section is "flipped": blocks slide in from the side they went toward
        static let pageTurn: Double = 9             // and big blocks tilt by this many degrees, like a page
        static let toastIcon: CGFloat = 20          // the icon in a notification
        static let toastPop: CGFloat = 0.2          // it pops out starting from this size
        static let toastInset: CGFloat = 0.26       // the checkmark's inset from the circle's edge, as a fraction of its size
        static let toastDraw = Animation.easeOut(duration: 0.34)   // the checkmark draws itself
        static let toastDrawDelay: Double = 0.1     // after the circle has already popped out
        static let toastErrorHold: Double = 5       // a notification held longer than this is about an error: an exclamation mark icon
        static let remove = AnyTransition.asymmetric(insertion: appear, removal: .scale(scale: 0.9).combined(with: .opacity).combined(with: .move(edge: .trailing)))   // a row slides sideways and fades
        static let shimmerPeriod: Double = 1.5      // seconds for one pass of a placeholder's shimmer
        static let shimmerBand: CGFloat = 0.6       // the running band's width, as a fraction of the placeholder
        static let shimmerStrength: Double = 0.35   // how bright the running band is
        static let skeletonFill: Double = 0.14      // placeholders' fill (ink with transparency)
        static let spinPeriod: Double = 0.9         // one turn of the loading ring, in seconds
        static let fade = Animation.easeOut(duration: 0.24)   // the old screen fades out on a section or tab change
        static let appear = AnyTransition.opacity.combined(with: .scale(scale: 0.98))   // a tag, card or row appears and disappears softly: the shared transition across the whole app
        static let splashEnter: CGFloat = 0.6       // the splash grows from this size
        static let splashExit: CGFloat = 1.06       // and it grows while fading out, once the server has answered
        static let intro: Double = 2.0              // the launch splash is shown for at least this long (enough for the leaf to assemble and breathe)
        static let introShort: Double = 0.4         // the same, with "reduce motion"
        static let introLimit: Double = 8           // we don't wait longer than this for the server: after that, the section itself shows the waiting state
        static let sonarPeriod: Double = 2.6        // seconds per ring from the tile
        static let sonarReach: CGFloat = 2.4        // how much bigger than the tile the ring ends up
        static let sonarOpacity: Double = 0.45      // the rings' brightness
        static let leafHover: CGFloat = 1.18        // the leaf in the menu header under the cursor
        static let leafAssemble = Animation.spring(response: 0.62, dampingFraction: 0.68)   // a petal settles into place
        static let leafStagger: Double = 0.075      // the delay between petals while assembling
        static let leafStart: CGFloat = 0.2         // the size a petal grows from
        static let leafTilt: Double = 38            // how many degrees it's rotated at first
        static let leafFly: CGFloat = 1.1           // where it flies in from, in leaf-sizes
        static let leafReplay: Double = 0.08        // the pause between "flew apart" and "came together" on a replay
        static let leafWave = (amplitude: 0.07, speed: 3.6, shift: 0.95)     // the breathing wave while loading: amplitude, speed, offset between petals
        static let leafBreath = (amplitude: 0.03, speed: 1.4, shift: 0.6)    // slow breathing while Sena is running
        static let leafSway = (degrees: 3.0, speed: 1.3)                     // the whole leaf swaying while loading
    }

    /// Charts (Views/Graphs.swift): a dashed grid with no axis lines, capsule bars with a gradient, a running total as a fill,
    /// a share ring. Changed here — applied to every chart at once
    enum Chart {
        static let height: CGFloat = 180           // the height of bars and the running total within a block
        static let line: CGFloat = 2               // a chart's line
        static let barWidth: CGFloat = 0.62        // a bar's width, as a fraction of its step
        static let gridDash: [CGFloat] = [2, 4]    // the grid's dash pattern
        static let ruleDash: [CGFloat] = [5, 4]    // the dash pattern for the average, forecast, and limit lines
        static let gridOpacity: Double = 0.16      // the grid: ink with transparency
        static let ruleOpacity: Double = 0.45      // the average line and the hover line
        static let barFade: Double = 0.55          // the bottom of a bar is paler than the top
        static let areaTop: Double = 0.38          // the fill under the line: denser at the top
        static let areaBottom: Double = 0.02       // and fading to nothing at the bottom
        static let dim: Double = 0.3               // the other bars and slices, while one is hovered
        static let pointArea: CGFloat = 56         // the area of the dot at the end of a line
        static let xLabels = 6                     // no more than this many labels on the X axis
        static let yLabels = 4                     // grid lines on the Y axis
        static let tipMuted: Double = 0.7          // the lesser half is paler: a tooltip's title beside its number, a journal chip's count beside its kind
        static let donut: CGFloat = 128            // the share ring: its size
        static let ring: CGFloat = 0.64            // the ring's hole, as a fraction of the radius
        static let sectorGap: CGFloat = 1.5        // the gap between slices
        static let sectorRadius: CGFloat = 3       // the slices' corner radius
        static let otherOpacity: Double = 0.3      // the "others" slice: muted ink
    }

    /// Colors. The system's .red/.blue, accentColor and the grays .secondary/.tertiary aren't used in screens — only from here
    enum Colors {
        static let card = Color(nsColor: .controlBackgroundColor)   // the main block
        /// Section blocks and the block inside a section: white cards (Artemiy's request: no coloring them), graphite in dark mode. The same color as paper
        static func block(_ s: ColorScheme) -> Color { Color(nsColor: s == .dark ? paperDark : paperLight) }
        static let blockShadow: Double = 0.12   // a block's shadow on the glass
        static let blockBorder: Double = 0.55   // a block's border — dark, reads as a frame
        /// A section's main block — contrasting: nearly black in light mode, nearly white in dark mode; the theme inside is inverted (text, tags)
        static func leadBlock(_ s: ColorScheme) -> Color { s == .dark ? Color(white: 0.96) : Color(nsColor: inkLight) }
        static func leadEdge(_ s: ColorScheme) -> Color { s == .dark ? Color.black.opacity(0.14) : Color.white.opacity(0.14) }   // a thin edge instead of a border
        static func opposite(_ s: ColorScheme) -> ColorScheme { s == .dark ? .light : .dark }
        /// Text color over a bright fill: dark on a light one, white on a dark one
        static func onFill(_ c: Color) -> Color {
            guard let n = NSColor(c).usingColorSpace(.sRGB) else { return .white }
            return 0.299 * n.redComponent + 0.587 * n.greenComponent + 0.114 * n.blueComponent > 0.55 ? Color(white: 0.1) : .white
        }

        /// The brand palette: mustard, coral, lime, sky, teal, lavender — for tiles, agents, tags
        static let palette = [Color(red: 0.98, green: 0.76, blue: 0.25), Color(red: 0.98, green: 0.47, blue: 0.42), Color(red: 0.56, green: 0.80, blue: 0.28),
                              Color(red: 0.40, green: 0.68, blue: 0.98), Color(red: 0.25, green: 0.78, blue: 0.72), Color(red: 0.66, green: 0.58, blue: 0.96)]
        static let tintOpacity: Double = 0.22   // a tile's pastel fill
        /// A stable color by key (a tile's label, a name): the same label is always the same color
        static func tint(_ key: String) -> Color {
            let hash = key.unicodeScalars.reduce(0) { $0 &* 31 &+ Int($1.value) }
            return palette[Int(hash.magnitude % UInt(palette.count))]   // magnitude, not abs: Int.min has no positive counterpart
        }
        /// Agent colors: ag_sena — mustard, ag_web — sky, ag_mac — teal, ag_code — lavender, ag_judge — coral, ag_night — lime, ag_ears — sky
        static func agent(_ code: String) -> Color { palette[agentIndex(code)] }
        static func agentIndex(_ code: String) -> Int {   // the color's index in the palette: the share ring needs it so colors don't repeat
            switch agentCode(code) {
            case "ag_sena": return 0; case "ag_web": return 3; case "ag_mac": return 4; case "ag_code": return 5
            case "ag_judge": return 1; case "ag_night": return 2; default: return 3
            }
        }
        /// The agents' characters by the notch (Character.swift), flat — the owner's own body colors from the logo project; the thought
        /// bubble is the body's color. Agents without a drawn color of their own get no character
        static let characters: [String: Color] = ["ag_sena": Color(red: 0.910, green: 0.710, blue: 0.278), "ag_web": Color(red: 0.122, green: 0.357, blue: 0.847),
                                                  "ag_mac": Color(red: 0.765, green: 0.780, blue: 0.804), "ag_code": Color(red: 0.478, green: 0.361, blue: 0.902)]
        static let selection = accent.opacity(0.14)   // a selected list row (the accent)
        static func sidebar(_ s: ColorScheme) -> Color { s == .dark ? Color(white: 0.13) : paper }   // the menu is the same white card as the rest
        static func pill(_ s: ColorScheme) -> Color { s == .dark ? Color(white: 0.95) : Color(nsColor: inkLight) }  // the selected section
        static func pillText(_ s: ColorScheme) -> Color { s == .dark ? Color(white: 0.10) : .white }
        static func hover(_ s: ColorScheme) -> Color { s == .dark ? Color.white.opacity(0.06) : Color.black.opacity(0.04) }
        static func grid(_ s: ColorScheme) -> Color { s == .dark ? Color.white.opacity(0.10) : Color.black.opacity(0.09) }   // the grid dots under the diagram
        static func lineStrong(_ s: ColorScheme) -> Color { s == .dark ? Color.white.opacity(0.45) : Color.black.opacity(0.4) }   // links on the diagram
        static let nodeBorder: Double = 0.18        // a diagram card's border — more visible than usual
        static let nodeShadow: Double = 0.14        // a diagram card's shadow

        // Opacity for integration board states (IntegrationsView.swift): a tile turned off by Tema or not connected,
        // the glow under a selected or working one, how dim a ribbon gets while blinking. The scene's own positions, sizes
        // and motion are position calculations, not styling, and stay in their own Look next to the scene
        static let boardDimDisabled: CGFloat = 0.35, boardDimOff: CGFloat = 0.6
        static let boardHaloSelected: CGFloat = 0.85, boardHaloWorking: CGFloat = 0.55
        static let boardPulseLow: CGFloat = 0.3
        static func leaf(_ s: ColorScheme) -> NSColor { s == .dark ? .white : .black }                               // the leaf in the menu header
        static let tile = Color(nsColor: NSColor(red: 0.078, green: 0.078, blue: 0.078, alpha: 1))                  // the tile with a leaf

        static let close = Color(red: 1.00, green: 0.37, blue: 0.34)      // window buttons — like the system ones
        static let minimize = Color(red: 1.00, green: 0.74, blue: 0.18)
        static let zoom = Color(red: 0.16, green: 0.78, blue: 0.25)
        static let windowOff = off.opacity(0.35)                 // window buttons while the window isn't active
        static let windowEdge = Color.black.opacity(0.12)        // a window button's thin border
        static let windowGlyph = Color.black.opacity(0.6)        // the icon on a window button

        // THE APP'S COLOR SET: the six palette colors above plus ink, paper and gray. Everything else is derived from them.
        // The system's .blue/.green/.red and accentColor aren't used in screens (the style test checks this)
        /// A color that changes itself with the theme (light / dark) — via a dynamic NSColor; also works with the app's forced theme
        static func dyn(_ light: NSColor, _ dark: NSColor) -> Color {
            Color(nsColor: NSColor(name: nil) { $0.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua ? dark : light })
        }
        static let inkLight = NSColor(white: 0.11, alpha: 1), inkDark = NSColor(white: 0.92, alpha: 1)      // for the scene: dynamic colors don't work there
        static let paperLight = NSColor.white, paperDark = NSColor(white: 0.16, alpha: 1)
        static let ink = dyn(inkLight, inkDark)             // text, dark "pills"; light in dark mode
        static let paper = dyn(paperLight, paperDark)       // cards, the search field
        static let accent = Color(red: 0.12, green: 0.42, blue: 0.92)   // the main accent: a saturated blue — buttons, selection, lists, switches (the palette's pastel sky is too pale for elements)
        static let ok = palette[2]                          // lime: done, running
        static let bad = palette[1]                         // coral: error, stopped, delete
        static let warn = palette[0]                        // mustard: with remarks, Sena's question
        /// The same "bad" and "warning", but as TEXT color: coral and mustard are fills, readable with black text over them, but as text on white they're pale (2–2.6:1)
        static let badText = dyn(NSColor(red: 0.78, green: 0.17, blue: 0.15, alpha: 1), NSColor(red: 1.0, green: 0.56, blue: 0.50, alpha: 1))
        static let warnText = dyn(NSColor(red: 0.60, green: 0.36, blue: 0.0, alpha: 1), NSColor(red: 1.0, green: 0.80, blue: 0.40, alpha: 1))
        static let model = palette[5]                       // lavender: model calls
        static let info = accent                            // tips, sends — the same blue as the accent
        // secondary and tertiary text: ink with transparency, darker than the system's .secondary/.tertiary (which read too pale)
        static let secondaryText = dyn(inkLight.withAlphaComponent(0.8), inkDark.withAlphaComponent(0.8))
        static let tertiaryText = dyn(inkLight.withAlphaComponent(0.62), inkDark.withAlphaComponent(0.62))
        static let off = Color(white: 0.62)                 // gray: off, no connection
        static let recording = palette[1]                   // recording a voice message
        static let theirs = accent.opacity(0.12)                     // chips for attachments and files (a pastel of the accent)

        /// The window's background: no blur, everything behind the window shows through sharply under a light veil — light in light mode, dark in dark mode
        static func veil(_ s: ColorScheme) -> Color { s == .dark ? Color.black.opacity(0.35) : Color.white.opacity(0.45) }
    }
}
