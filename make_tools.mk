
MAKEFLAGS += --no-print-directory

TOOLDIRS := $(patsubst %/Makefile,%,$(filter-out tools/agbcc/Makefile,$(wildcard tools/*/Makefile)))

.PHONY: all $(TOOLDIRS) clean

all: $(TOOLDIRS)

$(TOOLDIRS):
	@$(MAKE) -C $@

clean:
	@$(foreach tooldir,$(TOOLDIRS),$(MAKE) clean -C $(tooldir);)
