import * as ts from "typescript"
import * as fs from "fs"
import * as path from "path"

const SRC_DIR = path.join(__dirname, "src")
const TEXT_TAGS = new Set(["text", "span", "b", "strong", "i", "em", "u", "br", "a"])

function checkFile(filePath: string) {
  const content = fs.readFileSync(filePath, "utf-8")
  const sourceFile = ts.createSourceFile(filePath, content, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)

  function getTagName(node: ts.JsxOpeningElement | ts.JsxSelfClosingElement): string {
    return node.tagName.getText(sourceFile)
  }

  function visit(node: ts.Node, parentTag: string | null) {
    if (ts.isJsxElement(node)) {
      const tag = getTagName(node.openingElement)
      for (const child of node.children) {
        if (ts.isJsxElement(child)) {
          const childTag = getTagName(child.openingElement)
          if (tag === "text" && childTag === "text") {
            const { line, character } = sourceFile.getLineAndCharacterOfPosition(child.getStart(sourceFile))
            console.log(`[NESTED_TEXT_IN_TEXT] ${filePath}:${line + 1}:${character + 1}`)
          }
        }
        if (ts.isJsxText(child)) {
          const text = child.getText(sourceFile)
          // Check if there is non-whitespace text
          if (text.trim().length > 0 && !TEXT_TAGS.has(tag)) {
            const { line, character } = sourceFile.getLineAndCharacterOfPosition(child.getStart(sourceFile))
            console.log(`[RAW_TEXT_IN_${tag.toUpperCase()}] ${filePath}:${line + 1}:${character + 1} -> ${JSON.stringify(text.trim())}`)
          }
        } else if (ts.isJsxExpression(child)) {
          if (child.expression && !TEXT_TAGS.has(tag)) {
            const expr = child.expression
            const exprText = expr.getText(sourceFile)

            // If it's a call expression like .map(), check what the callback returns or if it's safe
            if (ts.isCallExpression(expr)) {
              // check if it's .map
              const prop = expr.expression
              if (ts.isPropertyAccessExpression(prop) && prop.name.text === "map") {
                // it's a map call, skip unless suspicious
              } else {
                const { line, character } = sourceFile.getLineAndCharacterOfPosition(expr.getStart(sourceFile))
                console.log(`[CALL_IN_${tag.toUpperCase()}] ${filePath}:${line + 1}:${character + 1} -> ${exprText.slice(0, 80)}`)
              }
            } else if (ts.isJsxElement(expr) || ts.isJsxSelfClosingElement(expr) || ts.isJsxFragment(expr)) {
              // safe jsx element
            } else if (ts.isBinaryExpression(expr) && expr.operatorToken.kind === ts.SyntaxKind.AmpersandAmpersandToken) {
              // Check left and right
              const left = expr.left.getText(sourceFile)
              const right = expr.right
              if (!ts.isJsxElement(right) && !ts.isJsxSelfClosingElement(right) && !ts.isJsxFragment(right) && !ts.isParenthesizedExpression(right)) {
                const { line, character } = sourceFile.getLineAndCharacterOfPosition(expr.getStart(sourceFile))
                console.log(`[SUSPICIOUS_AND_IN_${tag.toUpperCase()}] ${filePath}:${line + 1}:${character + 1} -> ${exprText.slice(0, 80)}`)
              }
              // Check if left is .length or a number that could evaluate to 0
              if (left.endsWith(".length") || left.includes("length >") === false && left.endsWith("count")) {
                const { line, character } = sourceFile.getLineAndCharacterOfPosition(expr.getStart(sourceFile))
                console.log(`[POSSIBLE_ZERO_LEAK_IN_${tag.toUpperCase()}] ${filePath}:${line + 1}:${character + 1} -> ${exprText.slice(0, 80)}`)
              }
            } else if (ts.isIdentifier(expr)) {
              const { line, character } = sourceFile.getLineAndCharacterOfPosition(expr.getStart(sourceFile))
              console.log(`[IDENTIFIER_IN_${tag.toUpperCase()}] ${filePath}:${line + 1}:${character + 1} -> ${exprText}`)
            } else {
              const { line, character } = sourceFile.getLineAndCharacterOfPosition(expr.getStart(sourceFile))
              console.log(`[EXPRESSION_IN_${tag.toUpperCase()}] ${filePath}:${line + 1}:${character + 1} -> ${exprText.slice(0, 80)}`)
            }
          }
        }
        visit(child, tag)
      }
    } else {
      ts.forEachChild(node, (c) => visit(c, parentTag))
    }
  }

  visit(sourceFile, null)
}

function scanDir(dir: string) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) {
      scanDir(full)
    } else if (entry.name.endsWith(".tsx")) {
      checkFile(full)
    }
  }
}

scanDir(SRC_DIR)
console.log("AST Scan Completed.")
