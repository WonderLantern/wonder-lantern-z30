/* ARM32 ABI 2. Native modules remain loaded until shutdown.
 * init must return 1 after registering a run handler. No asynchronous callbacks
 * or retained host stack pointers may be created through this interface. */
#ifndef Z30_MODULE_API_H
#define Z30_MODULE_API_H
#include <stdint.h>
#include <stddef.h>
struct z30_module_api;
typedef uint32_t (*z30_module_run)(const struct z30_module_api *, void *context);
struct z30_module_api {
    uint32_t version;
    uint32_t size;
    uint32_t (*get_property)(uint32_t key);
    /* NUL-terminated printable ASCII, at most 40 characters. Pointer must
     * refer to the loaded payload or the module's 64-byte context. */
    uint32_t (*set_text)(const char *text);
    /* Only callable during init; callback must be Thumb code in this payload. */
    uint32_t (*set_run_handler)(z30_module_run callback);
};
#if UINTPTR_MAX != UINT32_MAX
#error The camera ABI requires ARM32 pointers
#endif
_Static_assert(sizeof(struct z30_module_api) == 20, "ABI 2 layout");
_Static_assert(offsetof(struct z30_module_api, set_run_handler) == 16, "handler slot");
/* Return 1 on success. context is zero-filled, 64 bytes, and persists until
 * shutdown. All module code/data must be position independent. */
typedef uint32_t (*z30_module_init)(const struct z30_module_api *, void *context);
#endif
