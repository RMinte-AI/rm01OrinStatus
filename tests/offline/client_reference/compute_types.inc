typedef struct {
    uint8_t id;         /**< 核心 ID */
    uint8_t usage;      /**< 使用率 (0-100%) */
    uint16_t freq_mhz;  /**< 频率 (MHz) */
} ts_compute_cpu_core_t;

/**
 * @brief 内存信息
 */
typedef struct {
    uint32_t used_mb;   /**< 已使用 (MB) */
    uint32_t total_mb;  /**< 总量 (MB) */
} ts_compute_memory_t;

/**
 * @brief 功耗信息
 */
typedef struct {
    uint32_t current_mw;    /**< 当前功耗 (mW) */
    uint32_t average_mw;    /**< 平均功耗 (mW) */
} ts_compute_power_t;

/**
 * @brief AGX 完整监控数据
 */
typedef struct {
    char timestamp[TS_COMPUTE_MAX_TIMESTAMP_LEN];   /**< ISO 8601 时间戳 */
    
    /* CPU 信息 */
    struct {
        uint8_t core_count;                     /**< CPU 核心数 */
        ts_compute_cpu_core_t cores[TS_COMPUTE_MAX_CPU_CORES];  /**< 各核心数据 */
    } cpu;
    
    /* 内存信息 */
    struct {
        ts_compute_memory_t ram;    /**< RAM 使用 */
        ts_compute_memory_t swap;   /**< SWAP 使用 */
    } memory;
    
    /* 温度信息 (°C) */
    struct {
        float cpu;      /**< CPU 温度 */
        float soc0;     /**< SoC 传感器 0 */
        float soc1;     /**< SoC 传感器 1 */
        float soc2;     /**< SoC 传感器 2 */
        float tj;       /**< 结温 (Junction Temperature) */
    } temperature;
    
    /* 功耗信息 */
    struct {
        ts_compute_power_t gpu_soc;     /**< GPU+SoC 功耗 */
        ts_compute_power_t cpu_cv;      /**< CPU 功耗 */
        ts_compute_power_t sys_5v;      /**< 系统 5V 功耗 */
    } power;
    
    /* GPU 信息 */
    struct {
        uint8_t gr3d_freq_pct;  /**< 3D GPU 频率百分比 */
    } gpu;
    
    bool is_valid;              /**< 数据有效性 */
    uint64_t update_time_us;    /**< 更新时间 (微秒) */
} ts_compute_data_t;

