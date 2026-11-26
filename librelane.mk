# Tulip hook for the chip-di-owas LibreLane Makefile, included there only for
# STDCELL_LIB=tulip:
#   ifeq ($(STDCELL_LIB),tulip)
#   include $(ROOT_DIR)/vendor/tulip/librelane.mk
#   endif
#
# make LibreLane STDCELL_LIB=tulip
#   builds a PDK overlay in $(ROOT_DIR)/rundirs/pdk_tulip (the standard-cell
#   timing libs of $(PDK) replaced by the Tulip libs from libs/$(PDK), see
#   scripts/mk_pdk_overlay.sh), uses it as PDK_ROOT and writes the runs to
#   $(ROOT_DIR)/rundirs/librelane_top_tulip (ll_gui/ll_steps then open the
#   newest Tulip run). Nothing here is active without STDCELL_LIB=tulip.
ifeq ($(STDCELL_LIB),tulip)
TULIP_DIR := $(dir $(lastword $(MAKEFILE_LIST)))
TULIP_PDK_ROOT := $(ROOT_DIR)/rundirs/pdk_tulip
$(info $(shell $(TULIP_DIR)scripts/mk_pdk_overlay.sh $(PDK_ROOT) $(TULIP_PDK_ROOT) $(PDK)))
$(if $(wildcard $(TULIP_PDK_ROOT)/$(PDK)/libs.ref),,$(error Tulip PDK overlay failed))
override PDK_ROOT := $(TULIP_PDK_ROOT)
override RUNS_DIR := $(ROOT_DIR)/rundirs/librelane_top_tulip
$(shell mkdir -p $(RUNS_DIR))
override LATEST_RUNS_DIR := $(shell find $(RUNS_DIR) -maxdepth 1 -type d | sort | tail -n 1)
override FROM_DIR := $(shell find $(LATEST_RUNS_DIR) -maxdepth 1 -type d -name *$(shell echo $(subst .,"-",$(FROM_STEP)) | tr A-Z a-z))
override TO_DIR := $(shell find $(LATEST_RUNS_DIR) -maxdepth 1 -type d -name *$(shell echo $(subst .,"-",$(TO_STEP)) | tr A-Z a-z))

# this file is not a target (no implicit-rule search when make checks it)
$(TULIP_DIR)librelane.mk: ;
endif
