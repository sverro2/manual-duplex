// Test page for Manual Duplex. Compile with:
//   typst compile assets/test-page.typ assets/test-page.pdf
#set page(paper: "a4", margin: 2cm)
#set text(size: 16pt)

#let test-page(number, note) = {
  align(center, text(size: 28pt, weight: "bold")[↑ TOP ↑])
  v(1fr)
  align(center, text(size: 220pt, weight: "bold")[#number])
  align(center, note)
  v(1fr)
  align(center, text(size: 11pt, fill: gray)[Manual Duplex test page])
}

#test-page(1)[Front of sheet 1]
#pagebreak()
#test-page(2)[Back of sheet 1: should be behind page 1, the same way up]
#pagebreak()
#test-page(3)[Front of sheet 2: its back should stay blank]
