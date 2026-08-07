#ifndef A6F56DE4_ADCE_471D_AAF9_3A4CB7FA06EC
#define A6F56DE4_ADCE_471D_AAF9_3A4CB7FA06EC
#ifndef E067868C_5EE0_4497_A425_089BEA66E60B
#define E067868C_5EE0_4497_A425_089BEA66E60B
#include "ModelAlgorithm.h"
#include "SimpleAffectionParam.h"
#include "NumCpp.hpp"
#include <vector>
namespace basic
{
    namespace affection
    {
        namespace handler
        {
            struct AttentionHandlerTemp
            {
                nc::NdArray<double> attentionStore;
                std::vector<nc::NdArray<double>> rulers;
                std::vector<nc::NdArray<double>> featuresStore;
                double weight;
            };

            struct RelaxationHandlerTemp
            {
                nc::NdArray<double> relaxationStore;
                std::vector<nc::NdArray<double>> rulers;
                std::vector<nc::NdArray<double>> featuresStore;
                double weight;
            };

            struct MeditationHandlerTemp
            {
                nc::NdArray<double> meditationStore;
                std::vector<nc::NdArray<double>> rulers;
                std::vector<nc::NdArray<double>> featuresStore;
                double weight;
            };

            struct PressureHandlerTemp
            {
                nc::NdArray<double> pressureStore;
                nc::NdArray<double> hrFreqRateStore;
            };

            struct PleasureHandlerTemp
            {
                nc::NdArray<double> pleasureStore;
                std::vector<nc::NdArray<double>> rulers;
                std::vector<nc::NdArray<double>> featuresStore;
                double weight;
            };

            struct ArousalHandlerTemp
            {
                nc::NdArray<double> arousalStore;
                nc::NdArray<double> hrFreqRateStore;
                double weight;
            };

            struct CoherenceHandlerTemp
            {
                nc::NdArray<double> coherenceStore;
                nc::NdArray<double> hrvStore;
            };

            /// @brief 
            /// @param betaNorm β波能量标称值
            /// @param thetaNorm θ波能量标称值
            /// @param gammaNorm γ波能量标称值
            /// @param alphaNorm α波能量标称值
            /// @param tmp 缓存
            /// @param smoothScope 注意力值平滑范围
            /// @param adjScope 标尺修正范围（修正阶段长度）
            /// @param adjPeriod 标尺修正周期（每次修正间隔）
            /// @param offsetRatio 标尺偏置系数（取值范围：0~1，取零表示不使用标尺修正）
            /// @param initialVal 注意力初始值
            /// @return 
            double attentionHandler(double betaNorm, double thetaNorm, double gammaNorm, double alphaNorm,
                            AttentionHandlerTemp &tmp, int smoothScope = 10, int adjScope = 100,
                            int adjPeriod = 20, double offsetRatio = 1., double initialVal = 65.);

            /**
             * @brief 放松度处理器
             * 
             * @param alphaNorm α波能量标称值
             * @param gammaNorm γ波能量标称值
             * @param eeglAlphaNorm 左通道α波能量标称值
             * @param eegrAlphaNorm 右通道α波能量标称值
             * @param tmp 缓存
             * @param smoothScope 放松度值平滑范围
             * @param adjScope 标尺修正范围（修正阶段长度）
             * @param adjPeriod 标尺修正周期（每次修正间隔）
             * @param offsetRatio 标尺偏置系数（取值范围：0~1，取零表示不使用标尺修正）
             * @param initialVal 放松度初始值
             * @return double 
             */
            double relaxationHandler(double alphaNorm, double gammaNorm, double eeglAlphaNorm, double eegrAlphaNorm,
                                     RelaxationHandlerTemp &tmp, int smoothScope = 10, int adjScope = 100,
                                     int adjPeriod = 20, double offsetRatio = 1.0, double initialVal = 50.0);


            /// @brief 冥想度处理器
            /// @param alphaNorm α波能量标称值
            /// @param thetaNorm θ波能量标称值
            /// @param gammaNorm γ波能量标称值
            /// @param eeglAlphaNorm 左通道α波能量标称值
            /// @param eegrAlphaNorm 右通道α波能量标称值
            /// @param tmp 缓存
            /// @param smoothScope 冥想度值平滑范围
            /// @param adjScope 标尺修正范围（修正阶段长度）
            /// @param adjPeriod 标尺修正周期（每次修正间隔）
            /// @param offsetRatio 标尺偏置系数（取值范围：0~1，取零表示不使用标尺修正）
            /// @param initialVal 冥想度初始值
            /// @param stretchRatio 缩放尺度系数
            /// @return 
            double meditationHandler(double alphaNorm, double thetaNorm, double gammaNorm, double eeglAlphaNorm, double eegrAlphaNorm,
                                    MeditationHandlerTemp &tmp, int smoothScope = 30, int adjScope = 180, int adjPeriod = 30, 
                                    double offsetRatio = 1.0, double initialVal = 30.0, double stretchRatio = 1.5);
            /**
             * @brief 压力水平处理器
             * 
             * @param hrVal 心率值
             * @param hrvVal 心率变异性值
             * @param hrLf 心率低频能量
             * @param freqRate 心率低高频能量比
             * @param tmp 缓存
             * @param smoothScope 压力值平滑范围
             * @return double 
             */
            double pressureHandler(double hrVal, double hrvVal, double hrLf, double freqRate,
                                   PressureHandlerTemp &tmp, int smoothScope = 10);

            /**
             * @brief 愉悦度处理器
             * 
             * @param eeglAlphaNorm 左通道脑电α波能量标称值
             * @param eegrAlphaNorm 右通道脑电α波能量标称值
             * @param eeglThetaNorm 左通道脑电θ波能量标称值
             * @param eegrThetaNorm 右通道脑电θ波能量标称值
             * @param tmp 缓存
             * @param smoothScope 愉悦度值平滑范围
             * @param adjScope 标尺修正范围（修正阶段长度）
             * @param adjPeriod 标尺修正周期（每次修正间隔）
             * @param offsetRatio 标尺偏置系数（取值范围：0~1，取零表示不使用标尺修正）
             * @param initialVal 愉悦度初始值
             * @return double 
             */
            double pleasureHandler(double eeglAlphaNorm, double eegrAlphaNorm, double eeglThetaNorm, double eegrThetaNorm,
                                   PleasureHandlerTemp &tmp, int smoothScope = 20, int adjScope = 100,
                                   int adjPeriod = 20, double offsetRatio = 0.5, double initialVal = 50);

            /**
             * @brief 激活度处理器
             * 
             * @param hrVal 心率值
             * @param freqRate 心率低高频能量比
             * @param tmp 缓存
             * @param smoothScope 激活度值平滑范围
             * @param initialVal 愉悦度初始值
             * @return double 
             */
            double arousalHandler(double hrVal, double freqRate, ArousalHandlerTemp &tmp,
                                  int smoothScope = 20, double initialVal = 50);

            /**
             * @brief 和谐度处理器
             * 
             * @param syncCor 神经系统同步频率相关系数
             * @param tmp 缓存
             * @param smoothScope 和谐度值平滑范围
             * @return double 
             */
            double coherenceHandler(double syncCor, CoherenceHandlerTemp &tmp, int smoothScope = 5);

        }

    } // namespace affection

} // namespace basic

#endif /* E067868C_5EE0_4497_A425_089BEA66E60B */


#endif /* A6F56DE4_ADCE_471D_AAF9_3A4CB7FA06EC */
