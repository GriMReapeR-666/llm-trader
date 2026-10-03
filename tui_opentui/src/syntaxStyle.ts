import { SyntaxStyle, RGBA } from "@opentui/core"

export const syntaxTheme = SyntaxStyle.fromStyles({
  keyword: { fg: RGBA.fromHex("#bb9af7"), bold: true },
  string: { fg: RGBA.fromHex("#9ece6a") },
  comment: { fg: RGBA.fromHex("#565f89"), italic: true },
  number: { fg: RGBA.fromHex("#ff9e64") },
  function: { fg: RGBA.fromHex("#7aa2f7") },
  variable: { fg: RGBA.fromHex("#c0caf5") },
  type: { fg: RGBA.fromHex("#73daca") },
  operator: { fg: RGBA.fromHex("#89ddff") },
  property: { fg: RGBA.fromHex("#7dcfff") },
  default: { fg: RGBA.fromHex("#c0caf5") },
})
