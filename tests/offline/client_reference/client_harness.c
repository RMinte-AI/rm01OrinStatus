/* Host-only harness. Sensor reader and JSONPath come from the pinned client.
 * No ESP32 task, socket, variable store, fan or device is executed. */
#include <stdio.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <stdlib.h>
#include "cJSON.h"
#include "ts_jsonpath.h"
#include "esp_log.h"
#define TS_COMPUTE_MAX_TIMESTAMP_LEN 32
#define TS_COMPUTE_MAX_CPU_CORES 16
#include "compute_types.inc"
#include "parse_tegrastats_data.inc"

int main(void) {
    char input[20000];
    size_t n = fread(input, 1, sizeof(input) - 1, stdin);
    input[n] = 0;
    ts_compute_data_t data = {0};
    if (parse_tegrastats_data(input, &data) != ESP_OK) return 2;
    cJSON *root = cJSON_Parse(input);
    cJSON *tj = ts_jsonpath_get(root, "temperature.tj");
    printf("{\"compute_tj\":%.9g,\"mapped_tj\":%.9g,\"cpu_core_count\":%u,\"ram_used\":%u,\"old_power_gpu_soc\":%u}\n",
           data.temperature.tj, cJSON_GetNumberValue(tj), data.cpu.core_count,
           data.memory.ram.used_mb, data.power.gpu_soc.current_mw);
    cJSON_Delete(tj);
    cJSON_Delete(root);
    return 0;
}
