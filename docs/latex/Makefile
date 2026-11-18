report.pdf: report.tex $(wildcard figures/*.pdf)
	pdflatex -interaction=nonstopmode -halt-on-error report
	pdflatex -interaction=nonstopmode -halt-on-error report

.PHONY: clean
clean:
	rm -f report.aux report.log report.out
