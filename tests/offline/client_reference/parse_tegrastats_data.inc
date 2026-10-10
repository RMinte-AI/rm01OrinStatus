static esp_err_t parse_tegrastats_data(const char *json_str, ts_compute_data_t *data)
{
    if (json_str == NULL || data == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    
    cJSON *root = cJSON_Parse(json_str);
    if (root == NULL) {
        ESP_LOGE(TAG, "Failed to parse tegrastats JSON");
        return ESP_FAIL;
    }
    
    /* 时间戳 */
    cJSON *timestamp = cJSON_GetObjectItem(root, "timestamp");
    if (timestamp != NULL && cJSON_IsString(timestamp)) {
        strncpy(data->timestamp, timestamp->valuestring, 
                sizeof(data->timestamp) - 1);
    }
    
    /* CPU 数据 */
    cJSON *cpu = cJSON_GetObjectItem(root, "cpu");
    if (cpu != NULL && cJSON_IsArray(cpu)) {
        data->cpu.core_count = cJSON_GetArraySize(cpu);
        if (data->cpu.core_count > TS_COMPUTE_MAX_CPU_CORES) {
            data->cpu.core_count = TS_COMPUTE_MAX_CPU_CORES;
        }
        
        for (int i = 0; i < data->cpu.core_count; i++) {
            cJSON *core = cJSON_GetArrayItem(cpu, i);
            if (core != NULL && cJSON_IsObject(core)) {
                cJSON *usage = cJSON_GetObjectItem(core, "usage");
                cJSON *freq = cJSON_GetObjectItem(core, "freq");
                
                data->cpu.cores[i].id = i;
                if (usage != NULL) {
                    data->cpu.cores[i].usage = (uint8_t)cJSON_GetNumberValue(usage);
                }
                if (freq != NULL) {
                    data->cpu.cores[i].freq_mhz = (uint16_t)cJSON_GetNumberValue(freq);
                }
            }
        }
    }
    
    /* 内存数据 */
    cJSON *ram = cJSON_GetObjectItem(root, "ram");
    if (ram != NULL && cJSON_IsObject(ram)) {
        cJSON *used = cJSON_GetObjectItem(ram, "used");
        cJSON *total = cJSON_GetObjectItem(ram, "total");
        if (used != NULL) data->memory.ram.used_mb = (uint32_t)cJSON_GetNumberValue(used);
        if (total != NULL) data->memory.ram.total_mb = (uint32_t)cJSON_GetNumberValue(total);
    }
    
    cJSON *swap = cJSON_GetObjectItem(root, "swap");
    if (swap != NULL && cJSON_IsObject(swap)) {
        cJSON *used = cJSON_GetObjectItem(swap, "used");
        cJSON *total = cJSON_GetObjectItem(swap, "total");
        if (used != NULL) data->memory.swap.used_mb = (uint32_t)cJSON_GetNumberValue(used);
        if (total != NULL) data->memory.swap.total_mb = (uint32_t)cJSON_GetNumberValue(total);
    }
    
    /* 温度数据 */
    cJSON *temp = cJSON_GetObjectItem(root, "temperature");
    if (temp != NULL && cJSON_IsObject(temp)) {
        cJSON *cpu_temp = cJSON_GetObjectItem(temp, "cpu");
        cJSON *soc0 = cJSON_GetObjectItem(temp, "soc0");
        cJSON *soc1 = cJSON_GetObjectItem(temp, "soc1");
        cJSON *soc2 = cJSON_GetObjectItem(temp, "soc2");
        cJSON *tj = cJSON_GetObjectItem(temp, "tj");
        
        if (cpu_temp != NULL) data->temperature.cpu = (float)cJSON_GetNumberValue(cpu_temp);
        if (soc0 != NULL) data->temperature.soc0 = (float)cJSON_GetNumberValue(soc0);
        if (soc1 != NULL) data->temperature.soc1 = (float)cJSON_GetNumberValue(soc1);
        if (soc2 != NULL) data->temperature.soc2 = (float)cJSON_GetNumberValue(soc2);
        if (tj != NULL) data->temperature.tj = (float)cJSON_GetNumberValue(tj);
    }
    
    /* 功耗数据 */
    cJSON *power = cJSON_GetObjectItem(root, "power");
    if (power != NULL && cJSON_IsObject(power)) {
        cJSON *gpu_soc = cJSON_GetObjectItem(power, "GPU_SOC");
        cJSON *cpu_cv = cJSON_GetObjectItem(power, "CPU_CV");
        cJSON *sys_5v = cJSON_GetObjectItem(power, "SYS_5V");
        
        if (gpu_soc != NULL && cJSON_IsObject(gpu_soc)) {
            cJSON *cur = cJSON_GetObjectItem(gpu_soc, "current");
            cJSON *avg = cJSON_GetObjectItem(gpu_soc, "average");
            if (cur != NULL) data->power.gpu_soc.current_mw = (uint32_t)cJSON_GetNumberValue(cur);
            if (avg != NULL) data->power.gpu_soc.average_mw = (uint32_t)cJSON_GetNumberValue(avg);
        }
        
        if (cpu_cv != NULL && cJSON_IsObject(cpu_cv)) {
            cJSON *cur = cJSON_GetObjectItem(cpu_cv, "current");
            cJSON *avg = cJSON_GetObjectItem(cpu_cv, "average");
            if (cur != NULL) data->power.cpu_cv.current_mw = (uint32_t)cJSON_GetNumberValue(cur);
            if (avg != NULL) data->power.cpu_cv.average_mw = (uint32_t)cJSON_GetNumberValue(avg);
        }
        
        if (sys_5v != NULL && cJSON_IsObject(sys_5v)) {
            cJSON *cur = cJSON_GetObjectItem(sys_5v, "current");
            cJSON *avg = cJSON_GetObjectItem(sys_5v, "average");
            if (cur != NULL) data->power.sys_5v.current_mw = (uint32_t)cJSON_GetNumberValue(cur);
            if (avg != NULL) data->power.sys_5v.average_mw = (uint32_t)cJSON_GetNumberValue(avg);
        }
    }
    
    /* GPU 数据 */
    cJSON *gpu = cJSON_GetObjectItem(root, "gpu");
    if (gpu != NULL && cJSON_IsObject(gpu)) {
        cJSON *gr3d_freq = cJSON_GetObjectItem(gpu, "gr3d_freq");
        if (gr3d_freq != NULL) {
            data->gpu.gr3d_freq_pct = (uint8_t)cJSON_GetNumberValue(gr3d_freq);
        }
    }
    
    cJSON_Delete(root);
    return ESP_OK;
}
